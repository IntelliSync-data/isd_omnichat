# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    omnichat_sound = fields.Boolean(string='Omnichat Sound', default=True)
    omnichat_desktop = fields.Boolean(string='Omnichat Desktop Notifications', default=True)

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['omnichat_sound', 'omnichat_desktop']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['omnichat_sound', 'omnichat_desktop']

    @api.model
    def omnichat_set_preferences(self, sound=None, desktop=None):
        values = {}
        if sound is not None:
            values['omnichat_sound'] = bool(sound)
        if desktop is not None:
            values['omnichat_desktop'] = bool(desktop)
        if values:
            self.env.user.sudo().write(values)
        return True
