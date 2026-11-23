# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    omnichat_conversation_ids = fields.One2many('omnichat.conversation', 'partner_id', string='Chats')
    omnichat_conversation_count = fields.Integer(compute='_compute_omnichat_conversation_count')

    def _compute_omnichat_conversation_count(self):
        counts = dict(self.env['omnichat.conversation']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count']))
        for partner in self:
            partner.omnichat_conversation_count = counts.get(partner, 0)

    def action_view_omnichat(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('isd_omnichat.action_conversation')
        action['domain'] = [('partner_id', '=', self.id)]
        return action
