# -*- coding: utf-8 -*-
"""The six messaging platforms, behind one small interface.

Each provider checks that a webhook really came from its platform, turns the
payload into plain events, and sends text, images and files back. The rest
of the module never sees a platform's JSON.

An inbound event is a dict:
    kind              'message' (from the customer), 'echo' (sent by the shop
                      from somewhere else, e.g. the Zalo app), 'postback'
                      (a button the customer tapped), 'status' (delivery report)
    user_id           the customer's id on the platform
    conversation_ref  the platform's conversation id, when it has one
    name, avatar_url, phone
    msg_id            the platform's message id, to skip redeliveries
    text
    attachments       [{'type': 'image'|'file'|'video'|'audio'|'sticker',
                        'url' or 'media_id', 'name', 'mimetype'}]
    payload           what a tapped button carries
    timestamp         a naive UTC datetime, or None
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

_logger = logging.getLogger(__name__)

HANDOFF_PAYLOAD = 'OMNICHAT_HANDOFF'
TIMEOUT = 20


class ProviderError(Exception):
    """A send or an API call failed; the message is shown to the agent."""


def _http(method, url, **kwargs):
    """Every call to a platform goes through here, so tests can stand in for the network."""
    kwargs.setdefault('timeout', TIMEOUT)
    return requests.request(method, url, **kwargs)


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {'_status': response.status_code, '_text': (response.text or '')[:300]}


def _from_epoch(value, ms=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if ms or value > 1e11:
        value /= 1000.0
    return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)


def _hmac_hex(key, message):
    key = key.encode() if isinstance(key, str) else key
    message = message.encode() if isinstance(message, str) else message
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _same(a, b):
    return bool(a) and bool(b) and hmac.compare_digest(str(a).strip(), str(b).strip())


def event(kind='message', **values):
    base = {'kind': kind, 'user_id': '', 'conversation_ref': '', 'name': '', 'avatar_url': '', 'phone': '',
            'msg_id': '', 'text': '', 'attachments': [], 'payload': '', 'timestamp': None}
    base.update({k: v for k, v in values.items() if v is not None})
    return base


class Provider:
    platform = None
    #: whether the platform can show a "Talk to a person" button under a message
    buttons = False

    def __init__(self, account):
        self.account = account

    # -- webhooks
    def handle_get(self, params):
        """(status, body) for a platform's subscription check, or None."""
        return None

    def verify(self, raw, headers, url):
        raise NotImplementedError

    def parse(self, payload):
        raise NotImplementedError

    # -- sending
    def send_text(self, conversation, text, buttons=None):
        """Send text; returns the platform's message id."""
        raise NotImplementedError

    def send_attachment(self, conversation, attachment):
        """Send one ir.attachment as an image or a file; returns the message id."""
        raise NotImplementedError

    # -- the rest
    def download(self, item):
        """(content, mimetype, name) of an inbound attachment."""
        url = item.get('url')
        if not url:
            raise ProviderError('no url')
        res = _http('GET', url)
        if res.status_code != 200:
            raise ProviderError('download failed (%s)' % res.status_code)
        return res.content, res.headers.get('Content-Type', '').split(';')[0], item.get('name')

    def fetch_profile(self, user_id):
        return {}

    def test_connection(self):
        """A short description of what the credentials reach, or raise."""
        raise NotImplementedError

    def refresh_token(self):
        """Renew the access token if the platform has refresh tokens. True when renewed."""
        return False

    def setup_webhook(self):
        return False

    # -- helpers
    def _save_tokens(self, access, refresh=None, expires_in=None, expires_at=None):
        values = {'access_token': access}
        if refresh:
            values['refresh_token'] = refresh
        if expires_at:
            values['token_expires_at'] = expires_at
        elif expires_in:
            values['token_expires_at'] = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(expires_in))
        self.account.sudo().write(values)


# ---------------------------------------------------------------- Zalo OA
class ZaloProvider(Provider):
    platform = 'zalo'
    buttons = True
    API = 'https://openapi.zalo.me'
    OAUTH = 'https://oauth.zaloapp.com/v4/oa/access_token'
    ECHO_EVENTS = ('oa_send_text', 'oa_send_image', 'oa_send_gif', 'oa_send_file', 'oa_send_sticker',
                   'oa_send_list')

    def verify(self, raw, headers, url):
        """X-ZEvent-Signature: mac=sha256(app_id + body + timestamp + OA secret key)."""
        secret = self.account.webhook_secret
        if not secret:
            return False
        provided = (headers.get('X-ZEvent-Signature') or '').strip()
        if provided.startswith('mac='):
            provided = provided[4:]
        try:
            timestamp = str(json.loads(raw or b'{}').get('timestamp') or '')
        except ValueError:
            return False
        body = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        expected = hashlib.sha256(
            ('%s%s%s%s' % (self.account.app_id or '', body, timestamp, secret)).encode()).hexdigest()
        return _same(provided, expected)

    def parse(self, payload):
        name = payload.get('event_name') or ''
        message = payload.get('message') or {}
        sender = str((payload.get('sender') or {}).get('id') or '')
        recipient = str((payload.get('recipient') or {}).get('id') or '')
        when = _from_epoch(payload.get('timestamp'), ms=True)
        attachments = []
        for item in message.get('attachments') or []:
            kind = item.get('type') or 'file'
            data = item.get('payload') or {}
            if kind == 'gif':
                kind = 'image'
            if kind in ('image', 'file', 'video', 'audio', 'sticker'):
                attachments.append({'type': kind, 'url': data.get('url') or data.get('thumbnail'),
                                    'name': data.get('name'), 'mimetype': data.get('type')})
            elif kind == 'location':
                coords = data.get('coordinates') or {}
                message['text'] = (message.get('text') or '') + ' 📍 %s,%s' % (
                    coords.get('latitude'), coords.get('longitude'))
            elif kind == 'link':
                message['text'] = (message.get('text') or '') + ' ' + (data.get('url') or '')
        if name.startswith('user_send_'):
            return [event('message', user_id=sender, msg_id=message.get('msg_id'),
                          text=(message.get('text') or '').strip(), attachments=attachments, timestamp=when)]
        if name in self.ECHO_EVENTS:
            return [event('echo', user_id=recipient, msg_id=message.get('msg_id'),
                          text=(message.get('text') or '').strip(), attachments=attachments, timestamp=when)]
        return []

    def _call(self, method, path, retry=True, **kwargs):
        headers = dict(kwargs.pop('headers', {}), access_token=self.account.access_token or '')
        data = _json(_http(method, self.API + path, headers=headers, **kwargs))
        if data.get('error') == -216 and retry and self.refresh_token():
            return self._call(method, path, retry=False, headers=headers, **kwargs)
        if data.get('error') not in (0, None):
            raise ProviderError('Zalo %s: %s' % (data.get('error'), data.get('message')))
        return data.get('data') or {}

    def _send(self, user_id, message):
        data = self._call('POST', '/v3.0/oa/message/cs',
                          json={'recipient': {'user_id': user_id}, 'message': message})
        return str(data.get('message_id') or '')

    def send_text(self, conversation, text, buttons=None):
        message = {'text': text}
        if buttons:
            message['attachment'] = {'type': 'template', 'payload': {'buttons': [
                {'title': label, 'type': 'oa.query.show', 'payload': label} for label, _payload in buttons]}}
            try:
                return self._send(conversation.external_user_id, message)
            except ProviderError:
                message.pop('attachment')
        return self._send(conversation.external_user_id, message)

    def send_attachment(self, conversation, attachment):
        content = attachment.raw
        files = {'file': (attachment.name, content, attachment.mimetype)}
        if (attachment.mimetype or '').startswith('image/'):
            data = self._call('POST', '/v2.0/oa/upload/image', files=files)
            message = {'attachment': {'type': 'template', 'payload': {
                'template_type': 'media',
                'elements': [{'media_type': 'image', 'attachment_id': data.get('attachment_id')}]}}}
        else:
            data = self._call('POST', '/v2.0/oa/upload/file', files=files)
            message = {'attachment': {'type': 'file', 'payload': {'token': data.get('token')}}}
        return self._send(conversation.external_user_id, message)

    def fetch_profile(self, user_id):
        data = self._call('GET', '/v3.0/oa/user/detail', params={'data': json.dumps({'user_id': user_id})})
        avatars = data.get('avatars') or {}
        return {'name': data.get('display_name') or '',
                'avatar_url': avatars.get('240') or data.get('avatar') or ''}

    def refresh_token(self):
        account = self.account
        if not (account.app_id and account.app_secret and account.refresh_token):
            return False
        data = _json(_http('POST', self.OAUTH, headers={'secret_key': account.app_secret},
                           data={'app_id': account.app_id, 'grant_type': 'refresh_token',
                                 'refresh_token': account.refresh_token}))
        if not data.get('access_token'):
            _logger.warning('isd_omnichat: Zalo token refresh failed for %s: %s', account.name, data)
            return False
        # Zalo refresh tokens are single-use: the new one has to be kept
        self._save_tokens(data['access_token'], data.get('refresh_token'), data.get('expires_in'))
        return True

    def test_connection(self):
        data = self._call('GET', '/v2.0/oa/getoa')
        return data.get('name') or data.get('oa_id') or 'OK'


# ------------------------------------------------- Facebook page (Messenger)
class FacebookProvider(Provider):
    platform = 'facebook'
    buttons = True
    GRAPH = 'https://graph.facebook.com/v21.0'

    def handle_get(self, params):
        if params.get('hub.mode') == 'subscribe':
            if _same(params.get('hub.verify_token'), self.account.webhook_secret):
                return 200, params.get('hub.challenge') or ''
            return 403, ''
        return None

    def verify(self, raw, headers, url):
        provided = (headers.get('X-Hub-Signature-256') or '').replace('sha256=', '')
        return bool(self.account.app_secret) and _same(provided, _hmac_hex(self.account.app_secret, raw))

    def parse(self, payload):
        events = []
        page_id = str(self.account.external_id or '')
        for entry in payload.get('entry') or []:
            for item in entry.get('messaging') or []:
                sender = str((item.get('sender') or {}).get('id') or '')
                recipient = str((item.get('recipient') or {}).get('id') or '')
                when = _from_epoch(item.get('timestamp'), ms=True)
                if 'postback' in item:
                    postback = item['postback']
                    events.append(event('postback', user_id=sender, msg_id=postback.get('mid'),
                                        text=postback.get('title') or '', payload=postback.get('payload') or '',
                                        timestamp=when))
                    continue
                message = item.get('message')
                if not message:
                    continue
                attachments = [{'type': a.get('type') if a.get('type') in ('image', 'video', 'audio') else 'file',
                                'url': (a.get('payload') or {}).get('url')}
                               for a in message.get('attachments') or [] if (a.get('payload') or {}).get('url')]
                if message.get('is_echo'):
                    # Our own sends come back as echoes too: those carry our app id
                    if str(message.get('app_id') or '') == str(self.account.app_id or '-'):
                        continue
                    events.append(event('echo', user_id=recipient, msg_id=message.get('mid'),
                                        text=message.get('text') or '', attachments=attachments, timestamp=when))
                    continue
                if page_id and recipient and recipient != page_id:
                    continue
                quick = (message.get('quick_reply') or {}).get('payload') or ''
                events.append(event('postback' if quick else 'message', user_id=sender, msg_id=message.get('mid'),
                                    text=message.get('text') or '', attachments=attachments, payload=quick,
                                    timestamp=when))
        return events

    def _call(self, method, path, **kwargs):
        params = dict(kwargs.pop('params', {}), access_token=self.account.access_token or '')
        data = _json(_http(method, self.GRAPH + path, params=params, **kwargs))
        if data.get('error'):
            error = data['error']
            raise ProviderError('Facebook %s: %s' % (error.get('code'), error.get('message')))
        return data

    def send_text(self, conversation, text, buttons=None):
        message = {'text': text[:2000]}
        if buttons:
            message['quick_replies'] = [{'content_type': 'text', 'title': label[:20], 'payload': payload}
                                        for label, payload in buttons]
        data = self._call('POST', '/me/messages', json={
            'recipient': {'id': conversation.external_user_id}, 'messaging_type': 'RESPONSE', 'message': message})
        return data.get('message_id') or ''

    def send_attachment(self, conversation, attachment):
        kind = 'image' if (attachment.mimetype or '').startswith('image/') else 'file'
        data = self._call('POST', '/me/messages', data={
            'recipient': json.dumps({'id': conversation.external_user_id}),
            'messaging_type': 'RESPONSE',
            'message': json.dumps({'attachment': {'type': kind, 'payload': {'is_reusable': False}}}),
        }, files={'filedata': (attachment.name, attachment.raw, attachment.mimetype)})
        return data.get('message_id') or ''

    def fetch_profile(self, user_id):
        data = self._call('GET', '/%s' % user_id, params={'fields': 'first_name,last_name,name,profile_pic'})
        name = data.get('name') or ' '.join(filter(None, [data.get('first_name'), data.get('last_name')]))
        return {'name': name, 'avatar_url': data.get('profile_pic') or ''}

    def test_connection(self):
        data = self._call('GET', '/me', params={'fields': 'id,name'})
        if data.get('id') and not self.account.external_id:
            self.account.sudo().external_id = data['id']
        return data.get('name') or data.get('id') or 'OK'


# --------------------------------------------------- WhatsApp Cloud API
class WhatsAppProvider(Provider):
    platform = 'whatsapp'
    buttons = True
    GRAPH = 'https://graph.facebook.com/v21.0'

    handle_get = FacebookProvider.handle_get
    verify = FacebookProvider.verify

    def parse(self, payload):
        events = []
        for entry in payload.get('entry') or []:
            for change in entry.get('changes') or []:
                value = change.get('value') or {}
                phone_id = str((value.get('metadata') or {}).get('phone_number_id') or '')
                if self.account.external_id and phone_id and phone_id != str(self.account.external_id):
                    continue
                names = {c.get('wa_id'): (c.get('profile') or {}).get('name') for c in value.get('contacts') or []}
                for msg in value.get('messages') or []:
                    sender = msg.get('from') or ''
                    kind = msg.get('type')
                    text, payload_value, attachments = '', '', []
                    if kind == 'text':
                        text = (msg.get('text') or {}).get('body') or ''
                    elif kind == 'interactive':
                        reply = (msg.get('interactive') or {})
                        reply = reply.get('button_reply') or reply.get('list_reply') or {}
                        text, payload_value = reply.get('title') or '', reply.get('id') or ''
                    elif kind == 'button':
                        text = (msg.get('button') or {}).get('text') or ''
                        payload_value = (msg.get('button') or {}).get('payload') or ''
                    elif kind in ('image', 'document', 'audio', 'video', 'sticker'):
                        media = msg.get(kind) or {}
                        text = media.get('caption') or ''
                        attachments.append({'type': 'file' if kind == 'document' else kind,
                                            'media_id': media.get('id'), 'name': media.get('filename'),
                                            'mimetype': media.get('mime_type')})
                    elif kind == 'location':
                        loc = msg.get('location') or {}
                        text = '📍 %s,%s %s' % (loc.get('latitude'), loc.get('longitude'), loc.get('name') or '')
                    else:
                        text = '[%s]' % kind
                    events.append(event('postback' if payload_value else 'message', user_id=sender, phone=sender,
                                        name=names.get(sender) or '', msg_id=msg.get('id'), text=text,
                                        attachments=attachments, payload=payload_value,
                                        timestamp=_from_epoch(msg.get('timestamp'))))
                for status in value.get('statuses') or []:
                    errors = status.get('errors') or []
                    events.append(event('status', msg_id=status.get('id'), user_id=status.get('recipient_id'),
                                        payload=status.get('status'),
                                        text='; '.join(e.get('title') or e.get('message') or '' for e in errors)))
        return events

    def _call(self, method, path, **kwargs):
        headers = dict(kwargs.pop('headers', {}), Authorization='Bearer %s' % (self.account.access_token or ''))
        data = _json(_http(method, self.GRAPH + path, headers=headers, **kwargs))
        if data.get('error'):
            error = data['error']
            raise ProviderError('WhatsApp %s: %s' % (error.get('code'), error.get('message')))
        return data

    def _send(self, to, body):
        body = dict(body, messaging_product='whatsapp', recipient_type='individual', to=to)
        data = self._call('POST', '/%s/messages' % self.account.external_id, json=body)
        return ((data.get('messages') or [{}])[0]).get('id') or ''

    def send_text(self, conversation, text, buttons=None):
        if buttons and len(text) <= 1024:
            return self._send(conversation.external_user_id, {'type': 'interactive', 'interactive': {
                'type': 'button', 'body': {'text': text},
                'action': {'buttons': [{'type': 'reply', 'reply': {'id': payload, 'title': label[:20]}}
                                       for label, payload in buttons]}}})
        return self._send(conversation.external_user_id, {'type': 'text', 'text': {'body': text[:4096]}})

    def send_attachment(self, conversation, attachment):
        media = self._call('POST', '/%s/media' % self.account.external_id,
                           data={'messaging_product': 'whatsapp', 'type': attachment.mimetype},
                           files={'file': (attachment.name, attachment.raw, attachment.mimetype)})
        if (attachment.mimetype or '').startswith('image/'):
            body = {'type': 'image', 'image': {'id': media.get('id')}}
        else:
            body = {'type': 'document', 'document': {'id': media.get('id'), 'filename': attachment.name}}
        return self._send(conversation.external_user_id, body)

    def download(self, item):
        info = self._call('GET', '/%s' % item.get('media_id'))
        res = _http('GET', info.get('url'), headers={'Authorization': 'Bearer %s' % self.account.access_token})
        if res.status_code != 200:
            raise ProviderError('download failed (%s)' % res.status_code)
        return res.content, info.get('mime_type') or item.get('mimetype'), item.get('name')

    def test_connection(self):
        data = self._call('GET', '/%s' % self.account.external_id,
                          params={'fields': 'display_phone_number,verified_name'})
        return ' '.join(filter(None, [data.get('verified_name'), data.get('display_phone_number')])) or 'OK'


# -------------------------------------------------------------- Telegram
class TelegramProvider(Provider):
    platform = 'telegram'
    buttons = True
    API = 'https://api.telegram.org'

    def verify(self, raw, headers, url):
        return _same(headers.get('X-Telegram-Bot-Api-Secret-Token'), self.account.webhook_secret)

    def parse(self, payload):
        callback = payload.get('callback_query')
        if callback:
            chat = ((callback.get('message') or {}).get('chat') or {})
            self._safe_call('answerCallbackQuery', json={'callback_query_id': callback.get('id')})
            sender = callback.get('from') or {}
            return [event('postback', user_id=str(chat.get('id') or sender.get('id') or ''),
                          name=self._name(sender), payload=callback.get('data') or '',
                          msg_id='cb:%s' % callback.get('id'))]
        message = payload.get('message')
        if not message:
            return []
        chat = message.get('chat') or {}
        sender = message.get('from') or {}
        attachments = []
        if message.get('photo'):
            attachments.append({'type': 'image', 'media_id': message['photo'][-1].get('file_id')})
        for key, kind in (('document', 'file'), ('video', 'video'), ('voice', 'audio'), ('audio', 'audio')):
            if message.get(key):
                item = message[key]
                attachments.append({'type': kind, 'media_id': item.get('file_id'), 'name': item.get('file_name'),
                                    'mimetype': item.get('mime_type')})
        text = message.get('text') or message.get('caption') or ''
        if message.get('sticker'):
            text = text or (message['sticker'].get('emoji') or '') + ' [sticker]'
        contact = message.get('contact') or {}
        if contact:
            text = text or '☎ %s' % contact.get('phone_number')
        name = chat.get('title') or self._name(sender)
        return [event('message', user_id=str(chat.get('id') or ''), name=name, phone=contact.get('phone_number'),
                      msg_id='%s:%s' % (chat.get('id'), message.get('message_id')), text=text,
                      attachments=attachments, timestamp=_from_epoch(message.get('date')))]

    @staticmethod
    def _name(user):
        name = ' '.join(filter(None, [user.get('first_name'), user.get('last_name')]))
        return name or ('@%s' % user['username'] if user.get('username') else '')

    def _call(self, method, **kwargs):
        data = _json(_http('POST', '%s/bot%s/%s' % (self.API, self.account.access_token or '', method), **kwargs))
        if not data.get('ok'):
            raise ProviderError('Telegram: %s' % (data.get('description') or data.get('_text') or 'error'))
        return data.get('result')

    def _safe_call(self, method, **kwargs):
        try:
            return self._call(method, **kwargs)
        except Exception:  # noqa: BLE001 - acknowledging a tap is best effort
            return None

    def send_text(self, conversation, text, buttons=None):
        body = {'chat_id': conversation.external_user_id, 'text': text[:4096]}
        if buttons:
            body['reply_markup'] = {'inline_keyboard': [[{'text': label, 'callback_data': payload}]
                                                        for label, payload in buttons]}
        return str((self._call('sendMessage', json=body) or {}).get('message_id') or '')

    def send_attachment(self, conversation, attachment):
        image = (attachment.mimetype or '').startswith('image/')
        method, field = ('sendPhoto', 'photo') if image else ('sendDocument', 'document')
        result = self._call(method, data={'chat_id': conversation.external_user_id},
                            files={field: (attachment.name, attachment.raw, attachment.mimetype)})
        return str((result or {}).get('message_id') or '')

    def download(self, item):
        info = self._call('getFile', json={'file_id': item.get('media_id')}) or {}
        res = _http('GET', '%s/file/bot%s/%s' % (self.API, self.account.access_token, info.get('file_path')))
        if res.status_code != 200:
            raise ProviderError('download failed (%s)' % res.status_code)
        name = item.get('name') or (info.get('file_path') or '').rsplit('/', 1)[-1]
        return res.content, item.get('mimetype'), name

    def setup_webhook(self):
        self._call('setWebhook', json={'url': self.account.webhook_url,
                                       'secret_token': self.account.webhook_secret,
                                       'allowed_updates': ['message', 'callback_query']})
        return True

    def test_connection(self):
        me = self._call('getMe') or {}
        if me.get('id') and not self.account.external_id:
            self.account.sudo().external_id = str(me['id'])
        return '@%s' % me.get('username') if me.get('username') else 'OK'


# ---------------------------------------------------------------- Shopee
class ShopeeProvider(Provider):
    """Shopee Open Platform v2 seller chat. Needs a partner app approved for chat."""
    platform = 'shopee'
    LIVE = 'https://partner.shopeemobile.com'

    def _host(self):
        return (self.account.api_host or self.LIVE).rstrip('/')

    def verify(self, raw, headers, url):
        """Authorization: HMAC-SHA256(partner key, push url + '|' + body)."""
        key = self.account.app_secret
        body = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        return bool(key) and _same(headers.get('Authorization'),
                                   _hmac_hex(key, '%s|%s' % (self.account.webhook_url, body)))

    def parse(self, payload):
        data = payload.get('data') or {}
        content = data.get('content') if isinstance(data.get('content'), dict) else data
        if not content or not content.get('message_id'):
            return []
        shop_id = str(payload.get('shop_id') or self.account.external_id or '')
        from_shop = str(content.get('from_shop_id') or '')
        mtype = content.get('message_type') or 'text'
        body = content.get('content') or {}
        text, attachments = '', []
        if mtype == 'text':
            text = body.get('text') or ''
        elif mtype == 'image':
            attachments.append({'type': 'image', 'url': body.get('url') or body.get('image_url')})
        elif mtype == 'sticker':
            text = '[sticker]'
        elif mtype in ('item', 'product'):
            text = '[product %s]' % (body.get('item_id') or '')
        elif mtype == 'order':
            text = '[order %s]' % (body.get('order_sn') or '')
        else:
            text = body.get('text') or '[%s]' % mtype
        when = _from_epoch(content.get('created_timestamp'))
        if from_shop and from_shop == shop_id:
            return [event('echo', user_id=str(content.get('to_id') or ''),
                          conversation_ref=str(content.get('conversation_id') or ''),
                          msg_id=str(content.get('message_id')), text=text, attachments=attachments, timestamp=when)]
        return [event('message', user_id=str(content.get('from_id') or ''),
                      conversation_ref=str(content.get('conversation_id') or ''),
                      name=content.get('from_user_name') or '', msg_id=str(content.get('message_id')),
                      text=text, attachments=attachments, timestamp=when)]

    def _signed(self, path, with_token=True):
        account = self.account
        ts = int(time.time())
        base = '%s%s%s' % (account.app_id, path, ts)
        params = {'partner_id': int(account.app_id or 0), 'timestamp': ts}
        if with_token:
            base += '%s%s' % (account.access_token or '', account.external_id or '')
            params.update(access_token=account.access_token or '', shop_id=int(account.external_id or 0))
        params['sign'] = _hmac_hex(account.app_secret or '', base)
        return params

    def _call(self, method, path, retry=True, **kwargs):
        data = _json(_http(method, self._host() + path, params=self._signed(path), **kwargs))
        error = data.get('error')
        if error and retry and 'token' in str(error) + str(data.get('message')) and self.refresh_token():
            return self._call(method, path, retry=False, **kwargs)
        if error:
            raise ProviderError('Shopee %s: %s' % (error, data.get('message')))
        return data.get('response') or {}

    def _send(self, conversation, message_type, content):
        data = self._call('POST', '/api/v2/sellerchat/send_message', json={
            'to_id': int(conversation.external_user_id), 'message_type': message_type, 'content': content})
        return str(data.get('message_id') or '')

    def send_text(self, conversation, text, buttons=None):
        return self._send(conversation, 'text', {'text': text})

    def send_attachment(self, conversation, attachment):
        if not (attachment.mimetype or '').startswith('image/'):
            raise ProviderError('Shopee chat only takes text and images.')
        data = self._call('POST', '/api/v2/sellerchat/upload_image',
                          files={'file': (attachment.name, attachment.raw, attachment.mimetype)})
        return self._send(conversation, 'image', {'image_url': data.get('url')})

    def refresh_token(self):
        account = self.account
        if not (account.app_id and account.app_secret and account.refresh_token):
            return False
        path = '/api/v2/auth/access_token/get'
        data = _json(_http('POST', self._host() + path, params=self._signed(path, with_token=False), json={
            'refresh_token': account.refresh_token, 'partner_id': int(account.app_id),
            'shop_id': int(account.external_id or 0)}))
        if not data.get('access_token'):
            _logger.warning('isd_omnichat: Shopee token refresh failed for %s: %s', account.name, data)
            return False
        self._save_tokens(data['access_token'], data.get('refresh_token'), data.get('expire_in'))
        return True

    def test_connection(self):
        data = self._call('GET', '/api/v2/shop/get_shop_info')
        return data.get('shop_name') or 'OK'


# ----------------------------------------------------------- TikTok Shop
class TikTokProvider(Provider):
    """TikTok Shop Partner API (customer service). Needs an app with the Customer Service scope."""
    platform = 'tiktok'
    API = 'https://open-api.tiktokglobalshop.com'
    AUTH = 'https://auth.tiktok-shops.com/api/v2/token/refresh'
    EXPIRED = (105002, 36004004, 105001)

    def verify(self, raw, headers, url):
        """Authorization: HMAC-SHA256(app secret, app key + body), lowercase hex."""
        account = self.account
        body = raw if isinstance(raw, bytes) else (raw or '').encode()
        return bool(account.app_secret) and _same(
            headers.get('Authorization'), _hmac_hex(account.app_secret, (account.app_id or '').encode() + body))

    def parse(self, payload):
        if payload.get('type') != 14:
            return []
        data = payload.get('data') or {}
        if data.get('is_visible') is False:
            return []
        sender = data.get('sender') or {}
        role = sender.get('role')
        if role in ('SYSTEM', 'ROBOT'):
            return []
        try:
            content = json.loads(data.get('content') or '{}')
        except ValueError:
            content = {}
        mtype = data.get('type') or 'TEXT'
        text, attachments = '', []
        if mtype == 'IMAGE':
            attachments.append({'type': 'image', 'url': content.get('url')})
        elif mtype == 'VIDEO':
            attachments.append({'type': 'video', 'url': content.get('url')})
        elif mtype in ('PRODUCT_CARD', 'BUYER_ENTER_FROM_PRODUCT'):
            text = '[product %s]' % content.get('product_id')
        elif mtype in ('ORDER_CARD', 'BUYER_ENTER_FROM_ORDER'):
            text = '[order %s]' % content.get('order_id')
        else:
            text = content.get('content') or ('[%s]' % mtype.lower())
        conversation_id = str(data.get('conversation_id') or '')
        kind = 'message' if role == 'BUYER' else 'echo'
        # A TikTok buyer is known by the conversation: that is what a reply goes to
        return [event(kind, user_id=conversation_id, conversation_ref=conversation_id,
                      msg_id=str(data.get('message_id') or ''), text=text, attachments=attachments,
                      timestamp=_from_epoch(data.get('create_time')))]

    def _sign(self, path, params, body=b''):
        secret = self.account.app_secret or ''
        items = ''.join('%s%s' % (k, params[k]) for k in sorted(params) if k not in ('sign', 'access_token'))
        base = path + items
        if body:
            base += body.decode() if isinstance(body, bytes) else body
        return _hmac_hex(secret, secret + base + secret)

    def _call(self, method, path, retry=True, with_shop=True, json_body=None, files=None, params=None):
        account = self.account
        query = dict(params or {}, app_key=account.app_id or '', timestamp=str(int(time.time())))
        if with_shop and account.shop_cipher:
            query['shop_cipher'] = account.shop_cipher
        body = json.dumps(json_body, separators=(',', ':')) if json_body is not None else ''
        query['sign'] = self._sign(path, query, '' if files else body)
        headers = {'x-tts-access-token': account.access_token or ''}
        if json_body is not None:
            headers['Content-Type'] = 'application/json'
        data = _json(_http(method, self.API + path, params=query, headers=headers,
                           data=body or None, files=files))
        if data.get('code') in self.EXPIRED and retry and self.refresh_token():
            return self._call(method, path, retry=False, with_shop=with_shop, json_body=json_body,
                              files=files, params=params)
        if data.get('code') not in (0, None):
            raise ProviderError('TikTok Shop %s: %s' % (data.get('code'), data.get('message')))
        return data.get('data') or {}

    def _conversation_id(self, conversation):
        return conversation.external_conversation_id or conversation.external_user_id

    def send_text(self, conversation, text, buttons=None):
        data = self._call('POST', '/customer_service/202606/conversations/%s/messages' % self._conversation_id(
            conversation), json_body={'type': 'TEXT', 'content': json.dumps({'content': text[:2000]}),
                                      'sender_role': 'CUSTOMER_SERVICE'})
        return str(data.get('message_id') or '')

    def send_attachment(self, conversation, attachment):
        if not (attachment.mimetype or '').startswith('image/'):
            raise ProviderError('TikTok Shop chat only takes text and images.')
        image = self._call('POST', '/customer_service/202309/images/upload',
                           files={'data': (attachment.name, attachment.raw, attachment.mimetype)})
        data = self._call('POST', '/customer_service/202606/conversations/%s/messages' % self._conversation_id(
            conversation), json_body={'type': 'IMAGE', 'content': json.dumps(
                {'url': image.get('url'), 'width': image.get('width'), 'height': image.get('height')}),
                'sender_role': 'CUSTOMER_SERVICE'})
        return str(data.get('message_id') or '')

    def refresh_token(self):
        account = self.account
        if not (account.app_id and account.app_secret and account.refresh_token):
            return False
        data = _json(_http('GET', self.AUTH, params={
            'app_key': account.app_id, 'app_secret': account.app_secret,
            'refresh_token': account.refresh_token, 'grant_type': 'refresh_token'})).get('data') or {}
        if not data.get('access_token'):
            _logger.warning('isd_omnichat: TikTok Shop token refresh failed for %s', account.name)
            return False
        expires = _from_epoch(data.get('access_token_expire_in'))
        self._save_tokens(data['access_token'], data.get('refresh_token'), expires_at=expires)
        return True

    def test_connection(self):
        shops = self._call('GET', '/authorization/202309/shops', with_shop=False).get('shops') or []
        if not shops:
            return 'OK'
        shop = shops[0]
        if not self.account.shop_cipher:
            self.account.sudo().write({'shop_cipher': shop.get('cipher'),
                                       'external_id': self.account.external_id or shop.get('id')})
        return shop.get('name') or 'OK'


PROVIDERS = {cls.platform: cls for cls in (
    ZaloProvider, FacebookProvider, WhatsAppProvider, TelegramProvider, ShopeeProvider, TikTokProvider)}


def get_provider(account):
    return PROVIDERS[account.platform](account)
