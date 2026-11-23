# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class OmnichatMessage(models.Model):
    """One message, either way. To charge credit for what the shop sends, price
    "Create" on this model with the filter Direction = Sent."""
    _name = 'omnichat.message'
    _description = 'Omnichat Message'
    _order = 'id'
    _rec_name = 'body'

    conversation_id = fields.Many2one('omnichat.conversation', required=True, ondelete='cascade', index=True)
    account_id = fields.Many2one(related='conversation_id.account_id', store=True, index=True)
    direction = fields.Selection([('in', 'Received'), ('out', 'Sent'), ('note', 'Note')], required=True,
                                 index=True)
    author_kind = fields.Selection([
        ('customer', 'Customer'), ('agent', 'Agent'), ('bot', 'AI'), ('app', 'Platform App'), ('system', 'System'),
    ], string='From', required=True, default='customer')
    user_id = fields.Many2one('res.users', string='Agent', index=True)
    body = fields.Text()
    msg_type = fields.Selection([('text', 'Text'), ('image', 'Image'), ('file', 'File'), ('sticker', 'Sticker')],
                                string='Type', default='text')
    attachment_ids = fields.Many2many('ir.attachment', 'omnichat_message_attachment_rel', 'message_id',
                                      'attachment_id', string='Files')
    external_id = fields.Char(string='Platform Message ID', index=True, copy=False)
    state = fields.Selection([('received', 'Received'), ('pending', 'Sending'), ('sent', 'Sent'),
                              ('failed', 'Failed')], default='received', index=True)
    error = fields.Char()
    date = fields.Datetime(default=fields.Datetime.now, index=True)

    def _inbox_data(self):
        self.ensure_one()
        author = self.conversation_id.name if self.direction == 'in' else (
            self.user_id.name if self.author_kind == 'agent' else
            dict(self._fields['author_kind']._description_selection(self.env)).get(self.author_kind))
        return {
            'id': self.id,
            'direction': self.direction,
            'author_kind': self.author_kind,
            'author': author or '',
            'body': self.body or '',
            'type': self.msg_type,
            'date': fields.Datetime.to_string(self.date) if self.date else False,
            'state': self.state,
            'error': self.error or '',
            'attachments': [{
                'id': a.id, 'name': a.name, 'mimetype': a.mimetype,
                'is_image': (a.mimetype or '').startswith('image/'),
                'url': '/web/content/%s?download=false' % a.id,
                'download_url': '/web/content/%s?download=true' % a.id,
            } for a in self.attachment_ids.sudo()],
        }

    def action_resend(self):
        """Try a failed message again."""
        self.ensure_one()
        self.conversation_id.check_access('write')
        if self.state != 'failed' or self.direction != 'out':
            raise UserError(_('Only a failed message can be sent again.'))
        conv = self.conversation_id
        provider = conv.account_id._provider()
        try:
            if self.attachment_ids:
                external_id = provider.send_attachment(conv, self.attachment_ids[:1].sudo())
            else:
                external_id = provider.send_text(conv, self.body or '')
            self.sudo().write({'state': 'sent', 'external_id': external_id or False, 'error': False})
        except Exception as error:  # noqa: BLE001
            self.sudo().write({'error': str(error)[:500]})
        conv._omni_notify(message=self)
        return self._inbox_data()
