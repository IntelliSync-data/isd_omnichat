# -*- coding: utf-8 -*-
import hashlib
import hmac
import json

from odoo.tests import tagged

from odoo.addons.isd_omnichat.services import providers
from odoo.addons.isd_omnichat.services.providers import HANDOFF_PAYLOAD, ProviderError

from .common import FakeResponse, OmnichatCase


def hexmac(key, message):
    return hmac.new(key.encode(), message if isinstance(message, bytes) else message.encode(),
                    hashlib.sha256).hexdigest()


@tagged('post_install', '-at_install')
class TestProviders(OmnichatCase):

    # ------------------------------------------------------------------ Zalo
    def test_zalo_signature_and_parse(self):
        payload = {'app_id': 'zapp', 'event_name': 'user_send_text', 'timestamp': '1732300000000',
                   'sender': {'id': 'u1'}, 'recipient': {'id': 'oa1'},
                   'message': {'msg_id': 'z1', 'text': 'Cho anh bảng giá'}}
        raw = json.dumps(payload).encode()
        mac = hashlib.sha256(('zapp' + raw.decode() + '1732300000000' + 'oa-secret').encode()).hexdigest()
        provider = self.zalo._provider()
        self.assertTrue(provider.verify(raw, {'X-ZEvent-Signature': 'mac=' + mac}, ''))
        self.assertFalse(provider.verify(raw, {'X-ZEvent-Signature': 'mac=forged'}, ''))
        [ev] = provider.parse(payload)
        self.assertEqual((ev['kind'], ev['user_id'], ev['text'], ev['msg_id']),
                         ('message', 'u1', 'Cho anh bảng giá', 'z1'))
        echo = provider.parse(dict(payload, event_name='oa_send_text', sender={'id': 'oa1'}, recipient={'id': 'u1'}))
        self.assertEqual((echo[0]['kind'], echo[0]['user_id']), ('echo', 'u1'))
        image = provider.parse(dict(payload, event_name='user_send_image', message={
            'msg_id': 'z2', 'attachments': [{'type': 'image', 'payload': {'url': 'https://img/1.jpg'}}]}))
        self.assertEqual(image[0]['attachments'][0]['url'], 'https://img/1.jpg')
        self.assertEqual(provider.parse(dict(payload, event_name='follow')), [])

    def test_zalo_refreshes_an_expired_token_and_resends(self):
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'A'})
        with self.network() as net:
            answers = iter([FakeResponse({'error': -216, 'message': 'expired'}),
                            FakeResponse({'error': 0, 'data': {'message_id': 'sent1'}})])
            net.on('/message/cs', lambda *a, **k: next(answers))
            net.on('oauth.zaloapp.com', FakeResponse({'access_token': 'new', 'refresh_token': 'new-r',
                                                      'expires_in': 90000}))
            self.assertEqual(self.zalo._provider().send_text(conv, 'Dạ'), 'sent1')
            self.assertEqual(net.sent('/message/cs')[-1]['headers']['access_token'], 'new')
        self.assertEqual((self.zalo.access_token, self.zalo.refresh_token), ('new', 'new-r'))

    def test_zalo_buttons_fall_back_to_plain_text(self):
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'A'})
        with self.network() as net:
            net.on('/message/cs', lambda m, u, **k: FakeResponse(
                {'error': -201, 'message': 'bad'} if 'attachment' in k['json']['message'] else
                {'error': 0, 'data': {'message_id': 'plain'}}))
            self.assertEqual(self.zalo._provider().send_text(conv, 'Hi', [('Gặp nhân viên', HANDOFF_PAYLOAD)]),
                             'plain')

    # -------------------------------------------------------------- Facebook
    def test_facebook_subscription_signature_and_events(self):
        provider = self.facebook._provider()
        self.assertEqual(provider.handle_get({'hub.mode': 'subscribe', 'hub.verify_token': 'verify-me',
                                              'hub.challenge': '42'}), (200, '42'))
        self.assertEqual(provider.handle_get({'hub.mode': 'subscribe', 'hub.verify_token': 'nope'})[0], 403)
        payload = {'object': 'page', 'entry': [{'messaging': [
            {'sender': {'id': 'psid1'}, 'recipient': {'id': 'page1'}, 'timestamp': 1732300000000,
             'message': {'mid': 'f1', 'text': 'Hello'}},
            {'sender': {'id': 'psid1'}, 'recipient': {'id': 'page1'}, 'timestamp': 1732300000001,
             'message': {'mid': 'f2', 'text': 'Talk to a person', 'quick_reply': {'payload': HANDOFF_PAYLOAD}}},
            {'sender': {'id': 'page1'}, 'recipient': {'id': 'psid1'},
             'message': {'mid': 'f3', 'text': 'from our app', 'is_echo': True, 'app_id': 'fbapp'}},
            {'sender': {'id': 'page1'}, 'recipient': {'id': 'psid1'},
             'message': {'mid': 'f4', 'text': 'from the page inbox', 'is_echo': True, 'app_id': '263902037430900'}},
        ]}]}
        raw = json.dumps(payload).encode()
        self.assertTrue(provider.verify(raw, {'X-Hub-Signature-256': 'sha256=' + hexmac('fbsecret', raw)}, ''))
        self.assertFalse(provider.verify(raw, {'X-Hub-Signature-256': 'sha256=00'}, ''))
        events = provider.parse(payload)
        self.assertEqual([(e['kind'], e['msg_id']) for e in events],
                         [('message', 'f1'), ('postback', 'f2'), ('echo', 'f4')])
        self.assertEqual(events[1]['payload'], HANDOFF_PAYLOAD)

    def test_facebook_send_with_quick_reply_and_error(self):
        conv = self.Conversation.create({'account_id': self.facebook.id, 'external_user_id': 'psid1', 'name': 'B'})
        with self.network() as net:
            net.on('/me/messages', FakeResponse({'message_id': 'mid.1'}))
            self.assertEqual(self.facebook._provider().send_text(conv, 'Hi', [('Talk to a person', 'X')]), 'mid.1')
            body = net.sent('/me/messages')[0]['json']
            self.assertEqual(body['message']['quick_replies'][0]['payload'], 'X')
            net.on('/me/messages', FakeResponse({'error': {'code': 10, 'message': 'outside the 24h window'}}))
            with self.assertRaisesRegex(ProviderError, '24h'):
                self.facebook._provider().send_text(conv, 'Late')

    # -------------------------------------------------------------- WhatsApp
    def test_whatsapp_parse_messages_buttons_and_statuses(self):
        payload = {'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'phone1'},
            'contacts': [{'wa_id': '84909888999', 'profile': {'name': 'Chị Thảo'}}],
            'messages': [
                {'from': '84909888999', 'id': 'w1', 'timestamp': '1732300000', 'type': 'text', 'text': {'body': 'Hi'}},
                {'from': '84909888999', 'id': 'w2', 'timestamp': '1732300001', 'type': 'interactive',
                 'interactive': {'type': 'button_reply', 'button_reply': {'id': HANDOFF_PAYLOAD, 'title': 'Talk'}}},
                {'from': '84909888999', 'id': 'w3', 'timestamp': '1732300002', 'type': 'image',
                 'image': {'id': 'media9', 'mime_type': 'image/jpeg', 'caption': 'photo'}},
            ],
            'statuses': [{'id': 'out1', 'status': 'failed', 'recipient_id': '84909888999',
                          'errors': [{'title': 'Re-engagement message'}]}],
        }}]}]}
        events = self.whatsapp._provider().parse(payload)
        self.assertEqual([e['kind'] for e in events], ['message', 'postback', 'message', 'status'])
        self.assertEqual((events[0]['name'], events[0]['phone']), ('Chị Thảo', '84909888999'))
        self.assertEqual(events[1]['payload'], HANDOFF_PAYLOAD)
        self.assertEqual(events[2]['attachments'][0]['media_id'], 'media9')
        self.assertEqual(events[3]['text'], 'Re-engagement message')
        other = self.whatsapp._provider().parse({'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'another'}, 'messages': [{'from': '1', 'id': 'x', 'type': 'text'}]}}]}]})
        self.assertEqual(other, [], 'messages for another number are not ours')

    def test_whatsapp_downloads_media_with_the_token(self):
        with self.network() as net:
            net.on('/media9', FakeResponse({'url': 'https://lookaside/abc', 'mime_type': 'image/jpeg'}))
            net.on('lookaside', FakeResponse(content=b'JPEG', headers={'Content-Type': 'image/jpeg'}))
            content, mimetype, _name = self.whatsapp._provider().download({'media_id': 'media9'})
            self.assertEqual((content, mimetype), (b'JPEG', 'image/jpeg'))
            self.assertEqual(net.sent('lookaside')[0]['headers']['Authorization'], 'Bearer watoken')

    # -------------------------------------------------------------- Telegram
    def test_telegram_secret_parse_and_buttons(self):
        provider = self.telegram._provider()
        self.assertTrue(self.telegram.webhook_secret, 'a secret is made for Telegram')
        self.assertTrue(provider.verify(b'{}', {'X-Telegram-Bot-Api-Secret-Token': self.telegram.webhook_secret}, ''))
        self.assertFalse(provider.verify(b'{}', {}, ''))
        [ev] = provider.parse({'update_id': 1, 'message': {
            'message_id': 5, 'date': 1732300000, 'chat': {'id': 777, 'type': 'private'},
            'from': {'id': 777, 'first_name': 'Đức', 'last_name': 'Khánh'},
            'photo': [{'file_id': 'small'}, {'file_id': 'big'}], 'caption': 'xem giúp'}})
        self.assertEqual((ev['user_id'], ev['name'], ev['msg_id'], ev['text']), ('777', 'Đức Khánh', '777:5', 'xem giúp'))
        self.assertEqual(ev['attachments'][0]['media_id'], 'big')
        with self.network() as net:
            net.on('answerCallbackQuery', FakeResponse({'ok': True, 'result': True}))
            [tap] = provider.parse({'callback_query': {'id': 'cb1', 'data': HANDOFF_PAYLOAD,
                                                       'from': {'id': 777, 'first_name': 'Đức'},
                                                       'message': {'chat': {'id': 777}}}})
            self.assertEqual((tap['kind'], tap['payload']), ('postback', HANDOFF_PAYLOAD))
            conv = self.Conversation.create({'account_id': self.telegram.id, 'external_user_id': '777', 'name': 'D'})
            net.on('sendMessage', FakeResponse({'ok': True, 'result': {'message_id': 6}}))
            self.assertEqual(provider.send_text(conv, 'Dạ', [('Gặp nhân viên', HANDOFF_PAYLOAD)]), '6')
            markup = net.sent('sendMessage')[0]['json']['reply_markup']
            self.assertEqual(markup['inline_keyboard'][0][0]['callback_data'], HANDOFF_PAYLOAD)
            net.on('setWebhook', FakeResponse({'ok': True, 'result': True}))
            net.on('getMe', FakeResponse({'ok': True, 'result': {'id': 99, 'username': 'shop_bot'}}))
            self.telegram.action_test_connection()
            hook = net.sent('setWebhook')[0]['json']
            self.assertEqual((hook['url'], hook['secret_token']), (self.telegram.webhook_url, self.telegram.webhook_secret))
        self.assertEqual((self.telegram.state, self.telegram.subtitle), ('ok', '@shop_bot'))

    # ---------------------------------------------------------------- Shopee
    def test_shopee_push_signature_parse_and_signed_send(self):
        provider = self.shopee._provider()
        payload = {'shop_id': 9001, 'code': 10, 'timestamp': 1732300000, 'data': {'type': 'message', 'content': {
            'message_id': 's1', 'conversation_id': 'c1', 'from_id': 5005, 'from_user_name': 'ngocphuong',
            'from_shop_id': 0, 'to_id': 8008, 'to_shop_id': 9001, 'message_type': 'text',
            'content': {'text': 'Còn hàng không shop?'}, 'created_timestamp': 1732300000}}}
        raw = json.dumps(payload).encode()
        good = hexmac('partnerkey', '%s|%s' % (self.shopee.webhook_url, raw.decode()))
        self.assertTrue(provider.verify(raw, {'Authorization': good}, ''))
        self.assertFalse(provider.verify(raw, {'Authorization': 'bad'}, ''))
        [ev] = provider.parse(payload)
        self.assertEqual((ev['kind'], ev['user_id'], ev['conversation_ref'], ev['name'], ev['text']),
                         ('message', '5005', 'c1', 'ngocphuong', 'Còn hàng không shop?'))
        payload['data']['content'].update(from_shop_id=9001, from_id=8008, to_id=5005, message_id='s2')
        [echo] = provider.parse(payload)
        self.assertEqual((echo['kind'], echo['user_id']), ('echo', '5005'))
        conv = self.Conversation.create({'account_id': self.shopee.id, 'external_user_id': '5005', 'name': 'N'})
        with self.network() as net:
            net.on('send_message', FakeResponse({'error': '', 'response': {'message_id': 'sm1'}}))
            self.assertEqual(provider.send_text(conv, 'Còn ạ'), 'sm1')
            call = net.sent('send_message')[0]
            params = call['params']
            base = '2001/api/v2/sellerchat/send_message%sshtoken9001' % params['timestamp']
            self.assertEqual(params['sign'], hexmac('partnerkey', base))
            self.assertEqual(call['json'], {'to_id': 5005, 'message_type': 'text', 'content': {'text': 'Còn ạ'}})

    # ----------------------------------------------------------- TikTok Shop
    def test_tiktok_signature_parse_and_send(self):
        provider = self.tiktok._provider()
        payload = {'type': 14, 'tts_notification_id': 'n1', 'shop_id': '7001', 'timestamp': 1732300000, 'data': {
            'message_id': 't1', 'conversation_id': 'conv9', 'type': 'TEXT', 'index': '1',
            'content': json.dumps({'content': 'Bao giờ giao?'}), 'create_time': 1732300000, 'is_visible': True,
            'sender': {'im_user_id': 'buyer1', 'role': 'BUYER'}}}
        raw = json.dumps(payload).encode()
        self.assertTrue(provider.verify(raw, {'Authorization': hexmac('ttsecret', b'ttkey' + raw)}, ''))
        self.assertFalse(provider.verify(raw, {'Authorization': 'x'}, ''))
        [ev] = provider.parse(payload)
        self.assertEqual((ev['kind'], ev['user_id'], ev['text']), ('message', 'conv9', 'Bao giờ giao?'))
        payload['data']['sender']['role'] = 'SYSTEM'
        self.assertEqual(provider.parse(payload), [])
        self.assertEqual(provider.parse({'type': 1, 'data': {}}), [])
        conv = self.Conversation.create({'account_id': self.tiktok.id, 'external_user_id': 'conv9',
                                         'external_conversation_id': 'conv9', 'name': 'T'})
        with self.network() as net:
            net.on('/messages', FakeResponse({'code': 0, 'data': {'message_id': 'tm1'}}))
            self.assertEqual(provider.send_text(conv, 'Mai ạ'), 'tm1')
            call = net.sent('/messages')[0]
            self.assertIn('/customer_service/202606/conversations/conv9/messages', call['url'])
            self.assertEqual(call['headers']['x-tts-access-token'], 'tttoken')
            params = call['params']
            self.assertEqual(params['shop_cipher'], 'CIPHER')
            path = '/customer_service/202606/conversations/conv9/messages'
            items = ''.join('%s%s' % (k, params[k]) for k in sorted(params) if k != 'sign')
            expected = hexmac('ttsecret', 'ttsecret' + path + items + call['data'] + 'ttsecret')
            self.assertEqual(params['sign'], expected)
            self.assertEqual(json.loads(json.loads(call['data'])['content']), {'content': 'Mai ạ'})

    def test_every_platform_has_a_provider(self):
        platforms = {p for p, _label in self.env['omnichat.account']._fields['platform'].selection}
        self.assertEqual(platforms, set(providers.PROVIDERS))
