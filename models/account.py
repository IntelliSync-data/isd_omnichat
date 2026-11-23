# -*- coding: utf-8 -*-
import logging
import re
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.providers import PROVIDERS, ProviderError, get_provider

_logger = logging.getLogger(__name__)

PLATFORMS = [
    ('zalo', 'Zalo OA'),
    ('facebook', 'Facebook Page'),
    ('whatsapp', 'WhatsApp'),
    ('telegram', 'Telegram'),
    ('shopee', 'Shopee'),
    ('tiktok', 'TikTok Shop'),
]
# Letter on the badge and colour of each platform, as in the desktop app
PLATFORM_STYLE = {
    'zalo': ('Z', '#0068FF'),
    'facebook': ('F', '#7B2FF7'),
    'whatsapp': ('W', '#16A34A'),
    'telegram': ('Tg', '#2AABEE'),
    'shopee': ('S', '#EE4D2D'),
    'tiktok': ('T', '#111111'),
}
SELLER_CENTERS = {
    'shopee': 'https://banhang.shopee.vn/',
    'tiktok': 'https://seller-vn.tiktok.com/',
}
SECRET_FIELDS = ('app_secret', 'webhook_secret', 'access_token', 'refresh_token')


HONORIFICS = {'anh', 'chị', 'chi', 'em', 'cô', 'co', 'chú', 'chu', 'bác', 'bac', 'ông', 'ong', 'bà', 'ba',
              'mr', 'mrs', 'ms', 'miss', 'dr'}


def _initials(name):
    """Two letters for an avatar: "Chị Thảo Linh Boutique" gives TL, as the desktop app did."""
    name = re.sub(r'\(.*?\)|[•·|].*$', ' ', name or '')
    words = [w for w in name.split() if w[:1].isalnum()]
    if len(words) > 1 and words[0].lower().strip('.') in HONORIFICS:
        words = words[1:]
    if len(words) > 2:
        words = words[:2]
    if not words:
        return '?'
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


class OmnichatAccount(models.Model):
    """One account on one platform: a Zalo OA, a Facebook page, a WhatsApp number..."""
    _name = 'omnichat.account'
    _description = 'Omnichat Account'
    _order = 'sequence, id'

    name = fields.Char(required=True, help='The name agents see, e.g. "Zalo Kinh Doanh 1"')
    platform = fields.Selection(PLATFORMS, required=True, default='zalo')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, help='Archived: stops receiving messages')
    hidden = fields.Boolean(string='Hidden', help='Not shown in the inbox, still receives messages')
    image_128 = fields.Image(string='Avatar', max_width=128, max_height=128)
    subtitle = fields.Char(help='Shown under the name in the settings, e.g. the phone number or @username')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    user_ids = fields.Many2many('res.users', 'omnichat_account_users_rel', 'account_id', 'user_id',
                                string='Agents', domain=[('share', '=', False)],
                                help='Who sees and answers this account. Managers see every account.')
    seller_center_url = fields.Char(string='Seller Center',
                                    help='Opened by the "Seller Center" button, for Shopee and TikTok Shop')

    # Connection. The same fields carry each platform's own credentials; the form names them per platform.
    app_id = fields.Char(string='App ID', groups='isd_omnichat.group_omnichat_manager')
    app_secret = fields.Char(string='App Secret', groups='isd_omnichat.group_omnichat_manager')
    webhook_secret = fields.Char(string='Webhook Secret', groups='isd_omnichat.group_omnichat_manager', copy=False)
    access_token = fields.Char(groups='isd_omnichat.group_omnichat_manager', copy=False)
    refresh_token = fields.Char(groups='isd_omnichat.group_omnichat_manager', copy=False)
    token_expires_at = fields.Datetime(groups='isd_omnichat.group_omnichat_manager', copy=False)
    external_id = fields.Char(string='Platform ID', groups='isd_omnichat.group_omnichat_manager',
                              help='OA ID, page ID, WhatsApp phone number ID, bot ID or shop ID')
    shop_cipher = fields.Char(groups='isd_omnichat.group_omnichat_manager', copy=False)
    api_host = fields.Char(string='API Host', groups='isd_omnichat.group_omnichat_manager',
                           help='Shopee only: leave empty for the live API, or the test host while testing')
    webhook_token = fields.Char(required=True, copy=False, readonly=True, index=True,
                                default=lambda self: secrets.token_urlsafe(24))
    webhook_url = fields.Char(compute='_compute_webhook_url')
    state = fields.Selection([('draft', 'Not Tested'), ('ok', 'Connected'), ('error', 'Error')],
                             default='draft', readonly=True, copy=False)
    last_error = fields.Char(readonly=True, copy=False)

    # Auto-reply
    ai_enabled = fields.Boolean(string='AI Auto-Reply',
                                help='New conversations are answered by AI Chatbot until the customer asks for a '
                                     'person or an agent replies')
    ai_available = fields.Boolean(compute='_compute_ai_available')
    handoff_label = fields.Char(string='"Talk to a Person" Button', translate=True,
                                default=lambda self: _('Talk to a person'),
                                help='The button under AI replies. Typing it, or asking for a person, '
                                     'hands the conversation to an agent.')
    handoff_message = fields.Char(string='Handoff Reply', translate=True,
                                  default=lambda self: _('Sure, a member of our team will reply to you shortly.'))
    away_message = fields.Char(string='When No Agent Can Reply', translate=True,
                               help='Sent instead of an AI answer when AI cannot answer (no credit, an error)')

    conversation_ids = fields.One2many('omnichat.conversation', 'account_id')
    conversation_count = fields.Integer(compute='_compute_conversation_count')
    platform_letter = fields.Char(compute='_compute_style')
    platform_color = fields.Char(compute='_compute_style')
    initials = fields.Char(compute='_compute_style')

    _sql_constraints = [
        ('webhook_token_unique', 'unique(webhook_token)', 'The webhook address must be unique.'),
    ]

    @api.depends('webhook_token', 'platform')
    def _compute_webhook_url(self):
        base = (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        for account in self:
            account.webhook_url = '%s/omnichat/webhook/%s/%s' % (base, account.platform, account.webhook_token)

    def _compute_ai_available(self):
        available = 'isd.ai.chatbot.conversation' in self.env
        for account in self:
            account.ai_available = available

    def _compute_conversation_count(self):
        counts = dict(self.env['omnichat.conversation']._read_group(
            [('account_id', 'in', self.ids)], ['account_id'], ['__count']))
        for account in self:
            account.conversation_count = counts.get(account, 0)

    @api.depends('platform', 'name')
    def _compute_style(self):
        for account in self:
            letter, color = PLATFORM_STYLE.get(account.platform, ('?', '#64748B'))
            account.platform_letter = letter
            account.platform_color = color
            account.initials = _initials(account.name)

    @api.onchange('platform')
    def _onchange_platform(self):
        if not self.seller_center_url and self.platform in SELLER_CENTERS:
            self.seller_center_url = SELLER_CENTERS[self.platform]
        if self.platform == 'telegram' and not self.webhook_secret:
            self.webhook_secret = secrets.token_urlsafe(24)

    @api.model_create_multi
    def create(self, vals_list):
        order = [p for p, _label in PLATFORMS]
        for vals in vals_list:
            if 'sequence' not in vals and vals.get('platform') in order:
                # Zalo first, then Facebook... as in the desktop app
                vals['sequence'] = (order.index(vals['platform']) + 1) * 10
            if vals.get('platform') == 'telegram' and not vals.get('webhook_secret'):
                vals['webhook_secret'] = secrets.token_urlsafe(24)
            if not vals.get('seller_center_url') and vals.get('platform') in SELLER_CENTERS:
                vals['seller_center_url'] = SELLER_CENTERS[vals['platform']]
        return super().create(vals_list)

    def write(self, vals):
        if set(SECRET_FIELDS + ('app_id', 'external_id', 'platform')) & set(vals):
            vals.setdefault('state', 'draft')
        return super().write(vals)

    # ------------------------------------------------------------ provider
    def _provider(self):
        self.ensure_one()
        return get_provider(self.sudo())

    def action_test_connection(self):
        for account in self:
            try:
                provider = account._provider()
                if account.platform == 'telegram':
                    provider.setup_webhook()
                label = provider.test_connection()
            except (ProviderError, Exception) as error:  # noqa: BLE001 - any failure is shown
                account.sudo().write({'state': 'error', 'last_error': str(error)[:500]})
                raise UserError(_('The connection failed: %s', error)) from None
            account.sudo().write({'state': 'ok', 'last_error': False,
                                  'subtitle': account.subtitle or (label if label != 'OK' else False)})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'message': _('Connected.'), 'sticky': False},
        }

    def action_refresh_token(self):
        for account in self:
            if not account._provider().refresh_token():
                raise UserError(_('The token could not be renewed. Check the app credentials and refresh token.'))
        return True

    def action_open_seller_center(self):
        self.ensure_one()
        if not self.seller_center_url:
            raise UserError(_('Set the Seller Center address first.'))
        return {'type': 'ir.actions.act_url', 'url': self.seller_center_url, 'target': 'new'}

    def action_new_webhook_token(self):
        for account in self:
            account.webhook_token = secrets.token_urlsafe(24)
        return True

    def action_toggle_hidden(self):
        for account in self:
            account.hidden = not account.hidden
        return True

    def action_view_conversations(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('isd_omnichat.action_conversation')
        action['domain'] = [('account_id', '=', self.id)]
        return action

    @api.model
    def _cron_refresh_tokens(self):
        """Renew tokens that expire within the next day (Zalo, Shopee and TikTok tokens are short-lived)."""
        soon = fields.Datetime.now() + timedelta(hours=24)
        accounts = self.sudo().search([('platform', 'in', ('zalo', 'shopee', 'tiktok')),
                                       ('refresh_token', '!=', False),
                                       '|', ('token_expires_at', '=', False), ('token_expires_at', '<=', soon)])
        for account in accounts:
            try:
                account._provider().refresh_token()
            except Exception:  # noqa: BLE001
                _logger.exception('isd_omnichat: token refresh failed for %s', account.name)

    # ---------------------------------------------------------------- inbox
    def _agent_partners(self):
        """Who hears about this account's messages: its agents and the managers."""
        managers = self.env.ref('isd_omnichat.group_omnichat_manager').sudo().users
        users = (self.sudo().user_ids | managers).filtered(lambda u: u.active and not u.share)
        return users.partner_id

    @api.model
    def get_inbox_accounts(self):
        """The accounts the current user sees in the inbox rail, with unread counts."""
        accounts = self.search([('hidden', '=', False)])
        unread = dict(self.env['omnichat.conversation']._read_group(
            [('account_id', 'in', accounts.ids), ('unread_count', '>', 0)], ['account_id'], ['__count']))
        return [{
            'id': account.id,
            'name': account.name,
            'platform': account.platform,
            'platform_label': dict(self._fields['platform']._description_selection(self.env))[account.platform],
            'letter': account.platform_letter,
            'color': account.platform_color,
            'initials': account.initials,
            'has_avatar': bool(account.image_128),
            'subtitle': account.subtitle or '',
            'unread': unread.get(account, 0),
            'seller_center_url': account.seller_center_url or '',
            'ai': bool(account.ai_enabled and account.ai_available),
        } for account in accounts]

    @api.model
    def get_settings_accounts(self):
        """Every account, hidden ones included, grouped by platform for the settings dialog."""
        labels = dict(self._fields['platform']._description_selection(self.env))
        groups = []
        accounts = self.search([])
        for platform, _label in PLATFORMS:
            items = accounts.filtered(lambda a: a.platform == platform)
            if not items:
                continue
            letter, color = PLATFORM_STYLE[platform]
            groups.append({'platform': platform, 'label': labels[platform], 'letter': letter, 'color': color,
                           'accounts': [{'id': a.id, 'name': a.name, 'subtitle': a.subtitle or '',
                                         'initials': a.initials, 'has_avatar': bool(a.image_128),
                                         'hidden': a.hidden, 'state': a.state,
                                         'seller_center_url': a.seller_center_url or ''} for a in items]})
        return groups
