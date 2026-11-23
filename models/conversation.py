# -*- coding: utf-8 -*-
import base64
import logging
import re
import unicodedata
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import providers
from ..services.providers import HANDOFF_PAYLOAD

_logger = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024
AVATAR_COLORS = ['#2563EB', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#DB2777', '#4F46E5', '#CA8A04']
AI_SOURCES = {'zalo': 'zalo', 'facebook': 'facebook'}
# Asking for a person, on top of the "Talk to a person" button and AI Chatbot's own phrases
HANDOFF_WORDS = ('gap nhan vien', 'gap nguoi', 'nguoi that', 'tu van vien', 'nhan vien tu van',
                 'chat truc tiep', 'talk to a person', 'talk to a human', 'real person', 'human agent', 'agent')


def plain(text):
    """Lower case, no accents: for comparing what customers type."""
    text = unicodedata.normalize('NFD', (text or '').lower().replace('đ', 'd'))
    return ' '.join(''.join(c for c in text if unicodedata.category(c) != 'Mn').split())


def strip_markdown(text):
    """AI answers are written for a web widget; chat apps show the asterisks as they are."""
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', r'\1 (\2)', text or '')
    text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r'^\s*[*-]\s+', '• ', text, flags=re.M)
    return text.strip()


class OmnichatConversation(models.Model):
    """Everything one customer wrote to one account, and the answers."""
    _name = 'omnichat.conversation'
    _description = 'Omnichat Conversation'
    _order = 'last_message_at desc, id desc'

    name = fields.Char(required=True, default=lambda self: _('Customer'))
    account_id = fields.Many2one('omnichat.account', required=True, ondelete='cascade', index=True)
    platform = fields.Selection(related='account_id.platform', store=True)
    external_user_id = fields.Char(string='Customer ID', required=True, index=True,
                                   help="The customer's id on the platform")
    external_conversation_id = fields.Char(string='Platform Conversation', index=True)
    image_128 = fields.Image(string='Avatar', max_width=128, max_height=128)
    partner_id = fields.Many2one('res.partner', string='Contact', index=True, ondelete='set null')
    phone = fields.Char()
    user_id = fields.Many2one('res.users', string='Assigned To', index=True, domain=[('share', '=', False)])
    state = fields.Selection([('new', 'New'), ('open', 'In Progress'), ('done', 'Done')],
                             default='new', required=True, index=True)
    mode = fields.Selection([('ai', 'AI'), ('human', 'Agent')], default='human', required=True,
                            help='AI: AI Chatbot answers. Agent: a person answers.')
    tag_ids = fields.Many2many('omnichat.tag', string='Tags')
    message_ids = fields.One2many('omnichat.message', 'conversation_id', string='Messages')
    last_message_at = fields.Datetime(index=True)
    last_inbound_at = fields.Datetime(string='Last Customer Message')
    last_message_preview = fields.Char(string='Last Message')
    last_direction = fields.Selection([('in', 'Received'), ('out', 'Sent')])
    unread_count = fields.Integer(default=0)
    crm_lead_id = fields.Integer(string='CRM Lead', copy=False)
    initials = fields.Char(compute='_compute_initials')
    color = fields.Char(compute='_compute_initials')

    _sql_constraints = [
        ('customer_unique', 'unique(account_id, external_user_id)', 'One conversation per customer and account.'),
    ]

    @api.depends('name')
    def _compute_initials(self):
        from .account import _initials
        for conv in self:
            conv.initials = _initials(conv.name)
            conv.color = AVATAR_COLORS[(conv.id or 0) % len(AVATAR_COLORS)]

    # ================================================================ inbound
    @api.model
    def _omni_receive(self, account, events):
        """Store what a webhook brought, then answer or hand over. Returns a short note for the log."""
        Message = self.env['omnichat.message'].sudo()
        notes = []
        for ev in events:
            if ev['kind'] == 'status':
                self._omni_status(account, ev)
                continue
            if not ev.get('user_id'):
                notes.append('no customer id')
                continue
            if ev.get('msg_id') and Message.search_count([('account_id', '=', account.id),
                                                          ('external_id', '=', ev['msg_id'])]):
                notes.append('duplicate %s' % ev['msg_id'])
                continue
            conv = self._omni_get_or_create(account, ev)
            if ev['kind'] == 'echo':
                # Sent from the platform's own app, not through Odoo: never charged
                conv.with_context(isd_credit_skip=True)._omni_add_message('out', ev, author='app')
                notes.append('echo')
                continue
            message = conv._omni_add_message('in', ev)
            conv._omni_after_inbound(ev, message)
            notes.append('message from %s' % ev['user_id'])
        return '; '.join(notes)

    @api.model
    def _omni_status(self, account, ev):
        """WhatsApp tells us later when a message could not be delivered."""
        if ev.get('payload') != 'failed' or not ev.get('msg_id'):
            return
        message = self.env['omnichat.message'].sudo().search(
            [('account_id', '=', account.id), ('external_id', '=', ev['msg_id'])], limit=1)
        if message:
            message.write({'state': 'failed', 'error': ev.get('text') or _('Not delivered')})
            message.conversation_id._omni_notify(message=message)

    @api.model
    def _omni_get_or_create(self, account, ev):
        conv = self.sudo().search([('account_id', '=', account.id), ('external_user_id', '=', ev['user_id'])],
                                  limit=1)
        if conv:
            updates = {}
            if ev.get('conversation_ref') and conv.external_conversation_id != ev['conversation_ref']:
                updates['external_conversation_id'] = ev['conversation_ref']
            if ev.get('name') and conv.name in (_('Customer'), 'Customer'):
                updates['name'] = ev['name']
            if ev.get('phone') and not conv.phone:
                updates['phone'] = ev['phone']
            if updates:
                conv.write(updates)
            return conv
        profile = {'name': ev.get('name'), 'avatar_url': ev.get('avatar_url')}
        if not profile['name'] and ev['kind'] != 'echo':
            try:
                profile.update({k: v for k, v in account._provider().fetch_profile(ev['user_id']).items() if v})
            except Exception as error:  # noqa: BLE001 - a missing name never blocks a message
                _logger.info('isd_omnichat: no profile for %s on %s: %s', ev['user_id'], account.name, error)
        values = {
            'account_id': account.id,
            'external_user_id': ev['user_id'],
            'external_conversation_id': ev.get('conversation_ref') or False,
            'name': profile.get('name') or '%s %s' % (_('Customer'), ev['user_id'][-4:]),
            'phone': ev.get('phone') or False,
            'mode': 'ai' if account.ai_enabled and account.ai_available else 'human',
        }
        image = self._omni_fetch_image(profile.get('avatar_url'))
        if image:
            values['image_128'] = image
        if values['phone']:
            values['partner_id'] = self._omni_find_partner(values['phone']).id or False
        return self.sudo().create(values)

    @api.model
    def _omni_fetch_image(self, url):
        if not url:
            return False
        try:
            res = providers._http('GET', url, timeout=10)
            if res.status_code == 200 and res.headers.get('Content-Type', '').startswith('image/'):
                return base64.b64encode(res.content)
        except Exception:  # noqa: BLE001
            pass
        return False

    @api.model
    def _omni_find_partner(self, phone):
        digits = re.sub(r'\D', '', phone or '')
        if len(digits) < 8:
            return self.env['res.partner']
        tail = digits[-9:]
        # Numbers are stored as typed: "+84 909 888 999", "0909.888.999"...
        pattern = '%'.join(tail)
        candidates = self.env['res.partner'].sudo().search(
            ['|', ('phone', '=ilike', '%' + pattern), ('mobile', '=ilike', '%' + pattern)], limit=5)
        return candidates.filtered(
            lambda p: re.sub(r'\D', '', p.phone or '').endswith(tail)
            or re.sub(r'\D', '', p.mobile or '').endswith(tail))[:1]

    def _omni_add_message(self, direction, ev, author=None):
        self.ensure_one()
        values = {
            'conversation_id': self.id,
            'direction': direction,
            'author_kind': author or ('customer' if direction == 'in' else 'agent'),
            'body': ev.get('text') or '',
            'external_id': ev.get('msg_id') or False,
            'date': ev.get('timestamp') or fields.Datetime.now(),
            'state': 'received' if direction == 'in' else 'sent',
            'msg_type': 'text',
        }
        message = self.env['omnichat.message'].sudo().create(values)
        missing = []
        for item in ev.get('attachments') or []:
            attachment = self._omni_download(message, item)
            if attachment:
                message.attachment_ids = [(4, attachment.id)]
            elif item.get('url'):
                missing.append(item['url'])
        kinds = [item.get('type') for item in ev.get('attachments') or []]
        if kinds:
            message.msg_type = 'image' if kinds[0] == 'image' else ('sticker' if kinds[0] == 'sticker' else 'file')
        if missing:
            message.body = '\n'.join(filter(None, [message.body] + missing))
        self._omni_touch(message)
        return message

    def _omni_download(self, message, item):
        try:
            content, mimetype, name = self.account_id._provider().download(item)
        except Exception as error:  # noqa: BLE001 - the link is kept in the text instead
            _logger.info('isd_omnichat: could not download an attachment: %s', error)
            return False
        if not content or len(content) > MAX_DOWNLOAD:
            return False
        mimetype = mimetype or item.get('mimetype') or 'application/octet-stream'
        if not name:
            ext = mimetype.split('/')[-1].split(';')[0] if '/' in mimetype else 'bin'
            name = '%s.%s' % (item.get('type') or 'file', 'jpg' if ext == 'jpeg' else ext)
        return self.env['ir.attachment'].sudo().create({
            'name': name, 'raw': content, 'mimetype': mimetype,
            'res_model': 'omnichat.message', 'res_id': message.id,
        })

    def _omni_touch(self, message):
        """Bring the conversation up to date after a message."""
        preview = message.body or {'image': _('[Image]'), 'sticker': _('[Sticker]')}.get(message.msg_type, _('[File]'))
        values = {
            'last_message_at': message.date or fields.Datetime.now(),
            'last_message_preview': (preview or '')[:120],
            'last_direction': message.direction,
        }
        if message.direction == 'in':
            values['last_inbound_at'] = message.date or fields.Datetime.now()
            values['unread_count'] = self.unread_count + 1
            if self.state == 'done':
                # A new question: AI takes it again if the account uses AI
                values['state'] = 'new'
                if self.account_id.ai_enabled and self.account_id.ai_available:
                    values['mode'] = 'ai'
        self.sudo().write(values)

    def _omni_is_handoff(self, ev):
        if ev.get('payload') == HANDOFF_PAYLOAD:
            return True
        text = plain(ev.get('text'))
        if not text:
            return False
        if text == plain(self.account_id.handoff_label):
            return True
        if any(word == text or (len(word) > 6 and word in text) for word in HANDOFF_WORDS):
            return True
        try:
            from odoo.addons.isd_ai_chatbot.services.lead_extract import is_handoff_request
        except ImportError:
            return False
        return is_handoff_request(ev.get('text') or '')

    def _omni_after_inbound(self, ev, message):
        self.ensure_one()
        if self.mode == 'ai':
            if self._omni_is_handoff(ev):
                self._omni_handoff(reply=True)
            elif not (ev.get('text') or '').strip():
                # A photo or a file: AI cannot read it, a person can
                self._omni_handoff(reply=False)
            else:
                self._omni_ai_reply(ev['text'])
        self._omni_notify(message=message, inbound=True)

    def _omni_handoff(self, reply=True):
        self.ensure_one()
        self.sudo().write({'mode': 'human', 'state': 'new' if self.state == 'done' else self.state})
        if reply and self.account_id.handoff_message:
            self._omni_send_quietly(self.account_id.handoff_message)
        self._omni_add_note(_('The customer asked for a person.'))

    def _omni_ai_reply(self, text):
        """Answer with AI Chatbot. When it cannot (no credit, an error), an agent takes over."""
        self.ensure_one()
        account = self.account_id
        buttons = None
        provider = account._provider()
        if provider.buttons and account.handoff_label:
            buttons = [(account.handoff_label, HANDOFF_PAYLOAD)]
        try:
            with self.env.cr.savepoint():
                answer = self._omni_ai_answer(text)
                if not answer:
                    raise UserError(_('AI gave no answer.'))
                self._omni_send(answer, author='bot', buttons=buttons, raise_on_error=True)
        except Exception as error:  # noqa: BLE001 - whatever happened, a person answers instead
            _logger.info('isd_omnichat: AI reply failed on conversation %s: %s', self.id, error)
            self.sudo().mode = 'human'
            self._omni_add_note(_('AI could not answer (%s); an agent takes over.', str(error)[:200]))
            if account.away_message:
                self._omni_send_quietly(account.away_message)

    def _omni_ai_answer(self, text):
        if 'isd.ai.chatbot.conversation' not in self.env:
            return ''
        from odoo.addons.isd_ai_chatbot.services.engine import ChatEngine
        env = self.env(su=True)
        ai_conv = env['isd.ai.chatbot.conversation']._get_or_create(
            'omnichat:%s' % self.id, AI_SOURCES.get(self.platform, 'api'))
        result = ChatEngine(env).reply(ai_conv, text[:1000])
        return strip_markdown(result.get('reply') or '')

    def _omni_send_quietly(self, text):
        """A courtesy message (handoff, away): never charged, never blocks."""
        try:
            with self.env.cr.savepoint():
                self.with_context(isd_credit_skip=True)._omni_send(text, author='bot')
        except Exception:  # noqa: BLE001
            _logger.exception('isd_omnichat: could not send a courtesy message on conversation %s', self.id)

    def _omni_add_note(self, text):
        message = self.env['omnichat.message'].sudo().with_context(isd_credit_skip=True).create({
            'conversation_id': self.id, 'direction': 'note', 'author_kind': 'system',
            'body': text, 'state': 'sent', 'msg_type': 'text'})
        return message

    # =============================================================== outbound
    def _omni_send(self, body='', attachments=None, author='agent', buttons=None, raise_on_error=False):
        """Send text and/or attachments; one message record each, text first. Returns the messages."""
        self.ensure_one()
        provider = self.account_id._provider()
        Message = self.env['omnichat.message']
        messages = Message
        jobs = []
        if body and body.strip():
            jobs.append(('text', body.strip(), None))
        for attachment in attachments or []:
            jobs.append(('file', '', attachment))
        for kind, text, attachment in jobs:
            message = Message.create({
                'conversation_id': self.id,
                'direction': 'out',
                'author_kind': author,
                'user_id': self.env.user.id if author == 'agent' else False,
                'body': text,
                'state': 'pending',
                'msg_type': 'text' if kind == 'text' else (
                    'image' if (attachment.mimetype or '').startswith('image/') else 'file'),
            })
            copy = False
            if attachment:
                copy = attachment.sudo().copy({'res_model': 'omnichat.message', 'res_id': message.id})
                message.sudo().attachment_ids = [(4, copy.id)]
            try:
                if kind == 'text':
                    external_id = provider.send_text(self, text, buttons=buttons)
                else:
                    external_id = provider.send_attachment(self, copy.sudo())
                message.sudo().write({'state': 'sent', 'external_id': external_id or False, 'error': False})
            except Exception as error:  # noqa: BLE001 - shown on the message, with a resend button
                message.sudo().write({'state': 'failed', 'error': str(error)[:500]})
                if raise_on_error:
                    raise
            self._omni_touch(message)
            messages |= message
        return messages

    # ================================================================== bus
    def _omni_notify(self, message=None, inbound=False):
        for conv in self:
            payload = {'conversation_id': conv.id, 'account_id': conv.account_id.id,
                       'message_id': message.id if message else False, 'inbound': inbound,
                       'name': conv.name, 'preview': conv.last_message_preview or ''}
            for partner in conv.account_id._agent_partners():
                partner._bus_send('isd_omnichat/update', payload)

    # ============================================================ inbox API
    def _inbox_item(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'initials': self.initials,
            'color': self.color,
            'has_avatar': bool(self.image_128),
            'account_id': self.account_id.id,
            'account_name': self.account_id.name,
            'platform': self.platform,
            'platform_letter': self.account_id.platform_letter,
            'platform_color': self.account_id.platform_color,
            'preview': self.last_message_preview or '',
            'last_direction': self.last_direction or '',
            'last_at': fields.Datetime.to_string(self.last_message_at) if self.last_message_at else False,
            'unread': self.unread_count,
            'state': self.state,
            'mode': self.mode,
            'user': self.user_id.name or '',
            'tags': [{'id': t.id, 'name': t.name, 'color': t.color} for t in self.tag_ids],
        }

    @api.model
    def get_inbox_list(self, account_id=False, filter='active', search='', limit=60, offset=0):
        domain = []
        if account_id:
            domain.append(('account_id', '=', account_id))
        else:
            domain.append(('account_id.hidden', '=', False))
        if filter == 'unread':
            domain.append(('unread_count', '>', 0))
        elif filter == 'mine':
            domain.append(('user_id', '=', self.env.uid))
        elif filter == 'ai':
            domain.append(('mode', '=', 'ai'))
        elif filter == 'done':
            domain.append(('state', '=', 'done'))
        elif filter == 'active':
            domain.append(('state', '!=', 'done'))
        if search:
            domain += ['|', '|', '|', '|', ('name', 'ilike', search), ('phone', 'ilike', search),
                       ('partner_id.name', 'ilike', search), ('tag_ids.name', 'ilike', search),
                       ('last_message_preview', 'ilike', search)]
        convs = self.search(domain, limit=limit + 1, offset=offset)
        return {'items': [c._inbox_item() for c in convs[:limit]], 'has_more': len(convs) > limit}

    @api.model
    def get_unread_total(self):
        return self.search_count([('unread_count', '>', 0), ('account_id.hidden', '=', False)])

    def get_thread(self, before_id=False, after_id=False, limit=40):
        """The conversation header and its messages, newest last. Opening it marks it read."""
        self.ensure_one()
        domain = [('conversation_id', '=', self.id)]
        if before_id:
            domain.append(('id', '<', before_id))
        if after_id:
            domain.append(('id', '>', after_id))
        Message = self.env['omnichat.message']
        messages = Message.search(domain, order='id desc', limit=limit + 1)
        has_more = len(messages) > limit and not after_id
        if not before_id and self.unread_count:
            self.sudo().unread_count = 0
            self._omni_notify()
        users = self.account_id.sudo().user_ids | self.env.ref('isd_omnichat.group_omnichat_manager').sudo().users
        header = dict(self._inbox_item(), **{
            'phone': self.phone or '',
            'partner_id': self.partner_id.id or False,
            'partner_name': self.partner_id.display_name or '',
            'user_id': self.user_id.id or False,
            'agents': [{'id': u.id, 'name': u.name} for u in users.filtered(lambda u: not u.share)],
            'all_tags': [{'id': t.id, 'name': t.name, 'color': t.color} for t in self.env['omnichat.tag'].search([])],
            'ai_available': bool(self.account_id.ai_enabled and self.account_id.ai_available),
            'crm': 'crm.lead' in self.env,
            'crm_lead_id': self.crm_lead_id or False,
            'window_note': self._omni_window_note(),
            'seller_center_url': self.account_id.seller_center_url or '',
        })
        return {'conversation': header, 'has_more': has_more,
                'messages': [m._inbox_data() for m in reversed(messages[:limit])]}

    def _omni_window_note(self):
        """Facebook and WhatsApp only let a business answer within 24 hours of the customer's last message."""
        if self.platform not in ('facebook', 'whatsapp') or not self.last_inbound_at:
            return ''
        if fields.Datetime.now() - self.last_inbound_at > timedelta(hours=24):
            return _('More than 24 hours since the customer last wrote: the platform may refuse a reply.')
        return ''

    def action_send_message(self, body='', attachment_ids=None):
        """Send from the inbox. An agent replying takes the conversation over from AI."""
        self.ensure_one()
        self.check_access('write')
        attachments = self.env['ir.attachment'].browse(attachment_ids or []).exists()
        # Only files the agent uploaded, or the images of a quick reply they can see
        for attachment in attachments:
            if attachment.res_model == 'omnichat.quick.reply':
                self.env['omnichat.quick.reply'].browse(attachment.res_id).check_access('read')
            elif attachment.create_uid != self.env.user:
                raise UserError(_('This file cannot be sent from here.'))
        if not (body or '').strip() and not attachments:
            raise UserError(_('Write a message or add a file.'))
        values = {}
        if self.mode == 'ai':
            values['mode'] = 'human'
        if not self.user_id:
            values['user_id'] = self.env.uid
        if self.state == 'new':
            values['state'] = 'open'
        if values:
            self.sudo().write(values)
        messages = self._omni_send(body, attachments, author='agent')
        self._omni_notify(message=messages[-1:])
        return [m._inbox_data() for m in messages]

    def action_set_state(self, state):
        self.check_access('write')
        values = {'state': state}
        if state == 'done':
            values['unread_count'] = 0
        self.write(values)
        self._omni_notify()
        return True

    def action_set_mode(self, mode):
        self.check_access('write')
        if mode == 'ai' and not all(c.account_id.ai_enabled and c.account_id.ai_available for c in self):
            raise UserError(_('AI auto-reply is not switched on for this account.'))
        self.write({'mode': mode})
        self._omni_notify()
        return True

    def action_assign(self, user_id):
        self.check_access('write')
        self.write({'user_id': user_id or False})
        self._omni_notify()
        return True

    def action_set_tags(self, tag_ids):
        self.check_access('write')
        self.write({'tag_ids': [(6, 0, tag_ids or [])]})
        return True

    def action_create_tag(self, name):
        self.check_access('write')
        tag = self.env['omnichat.tag'].sudo().search([('name', '=ilike', name.strip())], limit=1) or \
            self.env['omnichat.tag'].sudo().create({'name': name.strip()})
        self.write({'tag_ids': [(4, tag.id)]})
        return {'id': tag.id, 'name': tag.name, 'color': tag.color}

    def action_rename(self, name):
        self.check_access('write')
        if name and name.strip():
            self.write({'name': name.strip()})
        return True

    def action_link_partner(self):
        """The customer's contact: the one already linked, one with the same phone, or a new one."""
        self.ensure_one()
        self.check_access('write')
        partner = self.partner_id or self._omni_find_partner(self.phone)
        if not partner:
            values = {'name': self.name, 'phone': self.phone or False, 'comment': _('From %s', self.account_id.name)}
            if self.image_128:
                values['image_1920'] = self.image_128
            partner = self.env['res.partner'].sudo().create(values)
        self.sudo().partner_id = partner
        return {'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'res_id': partner.id,
                'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}

    def action_create_lead(self):
        self.ensure_one()
        self.check_access('write')
        if 'crm.lead' not in self.env:
            raise UserError(_('Install CRM to create leads.'))
        Lead = self.env['crm.lead']
        lead = self.crm_lead_id and Lead.browse(self.crm_lead_id).exists()
        if not lead:
            lines = []
            for message in self.message_ids.sorted('id')[-30:]:
                if message.direction == 'note':
                    continue
                who = self.name if message.direction == 'in' else (message.user_id.name or _('Shop'))
                lines.append('<p><b>%s</b>: %s</p>' % (who, (message.body or '').replace('<', '&lt;')))
            lead = Lead.create({
                'name': _('%(name)s (%(account)s)', name=self.name, account=self.account_id.name),
                'partner_id': self.partner_id.id or False,
                'contact_name': self.name if not self.partner_id else False,
                'phone': self.phone or False,
                'user_id': self.env.uid,
                'description': ''.join(lines),
            })
            self.sudo().crm_lead_id = lead.id
        return {'type': 'ir.actions.act_window', 'res_model': 'crm.lead', 'res_id': lead.id,
                'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}

    def action_open_inbox(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'isd_omnichat.inbox',
                'params': {'conversation_id': self.id, 'account_id': self.account_id.id}}
