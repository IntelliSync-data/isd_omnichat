# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MAX_IMAGES = 6
SHORTCUT_RE = re.compile(r'^[\w-]{1,20}$', re.UNICODE)


class OmnichatQuickReply(models.Model):
    """A reply typed with \\shortcut in the chat box: its text, then up to 6 images."""
    _name = 'omnichat.quick.reply'
    _description = 'Omnichat Quick Reply'
    _order = 'shortcut'
    _rec_name = 'shortcut'

    shortcut = fields.Char(required=True, help='Typed after \\ in the chat box. Up to 20 letters, digits, - or _.')
    body = fields.Text(string='Message', required=True,
                       help='{name} becomes the customer name, {agent} the agent name')
    image_ids = fields.Many2many('ir.attachment', 'omnichat_quick_reply_image_rel', 'quick_reply_id',
                                 'attachment_id', string='Images')
    user_id = fields.Many2one('res.users', string='Owner', index=True,
                              default=lambda self: False if self.env.user.has_group(
                                  'isd_omnichat.group_omnichat_manager') else self.env.user,
                              help='Empty: shared with the whole team')
    active = fields.Boolean(default=True)
    image_count = fields.Integer(compute='_compute_image_count')

    @api.depends('image_ids')
    def _compute_image_count(self):
        for reply in self:
            reply.image_count = len(reply.image_ids)

    @api.constrains('shortcut', 'image_ids', 'user_id')
    def _check_reply(self):
        for reply in self:
            if not SHORTCUT_RE.match(reply.shortcut or ''):
                raise ValidationError(_('A shortcut is up to 20 letters, digits, - or _, without spaces.'))
            if len(reply.image_ids) > MAX_IMAGES:
                raise ValidationError(_('A quick reply takes up to %s images.', MAX_IMAGES))
            same = self.sudo().search_count([
                ('id', '!=', reply.id), ('shortcut', '=ilike', reply.shortcut),
                ('user_id', '=', reply.user_id.id or False)])
            if same:
                raise ValidationError(_('The shortcut \\%s is already used.', reply.shortcut))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('shortcut'):
                vals['shortcut'] = vals['shortcut'].strip().lstrip('\\')
        replies = super().create(vals_list)
        replies._attach_images()
        return replies

    def write(self, vals):
        if vals.get('shortcut'):
            vals['shortcut'] = vals['shortcut'].strip().lstrip('\\')
        res = super().write(vals)
        if 'image_ids' in vals:
            self._attach_images()
        return res

    def _attach_images(self):
        """Images uploaded from the inbox belong to the reply, so whoever can read it can send them."""
        for reply in self:
            reply.image_ids.sudo().filtered(lambda a: a.res_model != reply._name or a.res_id != reply.id).write(
                {'res_model': reply._name, 'res_id': reply.id})

    def _inbox_data(self):
        self.ensure_one()
        return {
            'id': self.id,
            'shortcut': self.shortcut,
            'body': self.body,
            'shared': not self.user_id,
            'mine': self.user_id == self.env.user,
            'images': [{'id': a.id, 'name': a.name, 'url': '/web/image/%s/128x128' % a.id}
                       for a in self.image_ids.sudo()],
        }

    @api.model
    def get_for_inbox(self):
        """The current user's replies first, then the shared ones; their own win on the same shortcut."""
        replies = self.search([])
        seen, result = set(), []
        for reply in replies.sorted(lambda r: (not r.user_id, r.shortcut.lower())):
            key = reply.shortcut.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(reply._inbox_data())
        return sorted(result, key=lambda r: r['shortcut'].lower())

    @api.model
    def save_from_inbox(self, reply_id, shortcut, body, image_ids, shared=False):
        values = {'shortcut': shortcut, 'body': body, 'image_ids': [(6, 0, image_ids or [])]}
        if self.env.user.has_group('isd_omnichat.group_omnichat_manager'):
            values['user_id'] = False if shared else self.env.uid
        if reply_id:
            reply = self.browse(reply_id)
            reply.write(values)
        else:
            values.setdefault('user_id', self.env.uid)
            reply = self.create(values)
        return reply._inbox_data()
