# -*- coding: utf-8 -*-
from odoo import api, fields, models


class OmnichatWebhookLog(models.Model):
    """Every call a platform makes to a webhook, to find out why a message did not arrive."""
    _name = 'omnichat.webhook.log'
    _description = 'Omnichat Webhook Log'
    _order = 'id desc'

    account_id = fields.Many2one('omnichat.account', ondelete='cascade', index=True)
    platform = fields.Char()
    status = fields.Selection([
        ('received', 'Received'), ('processed', 'Processed'), ('ignored', 'Ignored'),
        ('rejected', 'Rejected'), ('error', 'Error'),
    ], default='received', index=True)
    note = fields.Text()
    payload = fields.Text()
    remote_ip = fields.Char(string='From IP')

    @api.autovacuum
    def _gc_old_logs(self):
        """Keep 30 days."""
        self.sudo().search([('create_date', '<', fields.Datetime.subtract(fields.Datetime.now(), days=30))]).unlink()
