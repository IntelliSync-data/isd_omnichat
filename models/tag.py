# -*- coding: utf-8 -*-
from random import randint

from odoo import fields, models


class OmnichatTag(models.Model):
    _name = 'omnichat.tag'
    _description = 'Omnichat Tag'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    color = fields.Integer(default=lambda self: randint(1, 11))

    _sql_constraints = [('name_unique', 'unique(name)', 'This tag already exists.')]
