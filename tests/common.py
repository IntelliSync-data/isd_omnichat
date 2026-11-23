# -*- coding: utf-8 -*-
import json
from contextlib import contextmanager
from unittest.mock import patch

from odoo.tests import TransactionCase

from odoo.addons.isd_omnichat.services import providers


class FakeResponse:
    def __init__(self, data=None, status=200, content=b'', headers=None):
        self._data = data if data is not None else {}
        self.status_code = status
        self.content = content
        self.text = json.dumps(self._data)
        self.headers = headers or {'Content-Type': 'application/json'}

    def json(self):
        return self._data


class FakeNetwork:
    """Stands in for the platforms: records each call and answers from a list of rules."""

    def __init__(self):
        self.calls = []
        self.rules = []

    def on(self, needle, response):
        self.rules.append((needle, response))

    def __call__(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        for needle, response in reversed(self.rules):
            if needle in url:
                return response(method, url, **kwargs) if callable(response) else response
        return FakeResponse({})

    def sent(self, needle):
        return [c for c in self.calls if needle in c['url']]


class OmnichatCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US', tracking_disable=True))
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.agent = Users.create({
            'name': 'Omni Agent', 'login': 'omni_agent', 'email': 'agent@example.com', 'lang': 'en_US',
            'groups_id': [(6, 0, [cls.env.ref('isd_omnichat.group_omnichat_agent').id])],
        })
        cls.other_agent = Users.create({
            'name': 'Other Agent', 'login': 'omni_other', 'email': 'other@example.com', 'lang': 'en_US',
            'groups_id': [(6, 0, [cls.env.ref('isd_omnichat.group_omnichat_agent').id])],
        })
        Account = cls.env['omnichat.account']
        cls.zalo = Account.create({
            'name': 'Zalo Kinh Doanh', 'platform': 'zalo', 'app_id': 'zapp', 'app_secret': 'zsecret',
            'webhook_secret': 'oa-secret', 'access_token': 'ztoken', 'refresh_token': 'zrefresh',
            'external_id': 'oa1', 'user_ids': [(6, 0, cls.agent.ids)],
        })
        cls.facebook = Account.create({
            'name': 'Fanpage', 'platform': 'facebook', 'app_id': 'fbapp', 'app_secret': 'fbsecret',
            'webhook_secret': 'verify-me', 'access_token': 'pagetoken', 'external_id': 'page1',
            'user_ids': [(6, 0, cls.agent.ids)],
        })
        cls.whatsapp = Account.create({
            'name': 'WhatsApp', 'platform': 'whatsapp', 'app_secret': 'wasecret', 'webhook_secret': 'wa-verify',
            'access_token': 'watoken', 'external_id': 'phone1', 'user_ids': [(6, 0, cls.agent.ids)],
        })
        cls.telegram = Account.create({
            'name': 'Telegram CSKH', 'platform': 'telegram', 'access_token': '123:abc',
            'user_ids': [(6, 0, cls.agent.ids)],
        })
        cls.shopee = Account.create({
            'name': 'Shopee Mall', 'platform': 'shopee', 'app_id': '2001', 'app_secret': 'partnerkey',
            'access_token': 'shtoken', 'refresh_token': 'shrefresh', 'external_id': '9001',
            'user_ids': [(6, 0, cls.other_agent.ids)],
        })
        cls.tiktok = Account.create({
            'name': 'TikTok Shop', 'platform': 'tiktok', 'app_id': 'ttkey', 'app_secret': 'ttsecret',
            'access_token': 'tttoken', 'shop_cipher': 'CIPHER', 'external_id': '7001',
            'user_ids': [(6, 0, cls.agent.ids)],
        })
        cls.Conversation = cls.env['omnichat.conversation']

    @contextmanager
    def network(self):
        fake = FakeNetwork()
        with patch.object(providers, '_http', fake):
            yield fake

    def receive(self, account, events):
        return self.Conversation.sudo()._omni_receive(account, events)

    def zalo_text(self, user='u1', text='Xin chào', msg_id='m1'):
        return providers.event('message', user_id=user, text=text, msg_id=msg_id, name='Anh Minh')
