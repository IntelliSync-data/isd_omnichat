# -*- coding: utf-8 -*-
import base64
import hashlib
import json

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.isd_omnichat.models.conversation import OmnichatConversation, strip_markdown
from odoo.addons.isd_omnichat.services import providers
from odoo.addons.isd_omnichat.services.providers import HANDOFF_PAYLOAD

from .common import FakeResponse, OmnichatCase

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install')
class TestInbox(OmnichatCase):

    def zalo_ok(self, net, message_id='sent'):
        net.on('/message/cs', FakeResponse({'error': 0, 'data': {'message_id': message_id}}))
        net.on('/oa/user/detail', FakeResponse({'error': 0, 'data': {'display_name': 'Anh Minh Hoàng'}}))

    # ----------------------------------------------------------- receiving
    def test_receive_creates_conversation_once(self):
        with self.network() as net:
            self.zalo_ok(net)
            self.receive(self.zalo, [providers.event('message', user_id='u1', text='Chào shop', msg_id='m1')])
            self.receive(self.zalo, [providers.event('message', user_id='u1', text='Chào shop', msg_id='m1')])
            self.receive(self.zalo, [providers.event('message', user_id='u1', text='Còn hàng?', msg_id='m2')])
        conv = self.Conversation.search([('account_id', '=', self.zalo.id)])
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv.name, 'Anh Minh Hoàng', 'the name comes from the Zalo profile')
        self.assertEqual(conv.message_ids.mapped('body'), ['Chào shop', 'Còn hàng?'], 'a redelivery is skipped')
        self.assertEqual((conv.unread_count, conv.state, conv.mode), (2, 'new', 'human'))
        self.assertEqual(conv.last_message_preview, 'Còn hàng?')

    def test_attachments_are_downloaded(self):
        with self.network() as net:
            self.zalo_ok(net)
            net.on('https://img/', FakeResponse(content=PNG, headers={'Content-Type': 'image/png'}))
            self.receive(self.zalo, [providers.event('message', user_id='u1', msg_id='m1', name='A',
                                                     attachments=[{'type': 'image', 'url': 'https://img/1.png'}])])
        message = self.Conversation.search([('account_id', '=', self.zalo.id)]).message_ids
        self.assertEqual((message.msg_type, message.attachment_ids.mimetype), ('image', 'image/png'))
        self.assertEqual(message.conversation_id.last_message_preview, '[Image]')

    def test_echo_is_stored_as_sent_from_the_app(self):
        with self.network() as net:
            self.zalo_ok(net)
            self.receive(self.zalo, [providers.event('echo', user_id='u1', text='Trả lời từ app Zalo', msg_id='e1')])
        message = self.Conversation.search([('account_id', '=', self.zalo.id)]).message_ids
        self.assertEqual((message.direction, message.author_kind, message.state), ('out', 'app', 'sent'))
        self.assertEqual(message.conversation_id.unread_count, 0)

    def test_partner_found_by_phone(self):
        partner = self.env['res.partner'].create({'name': 'Chị Thảo', 'mobile': '+84 909 888 999'})
        self.receive(self.whatsapp, [providers.event('message', user_id='84909888999', phone='84909888999',
                                                     name='Thảo', text='Hi', msg_id='w1')])
        conv = self.Conversation.search([('account_id', '=', self.whatsapp.id)])
        self.assertEqual(conv.partner_id, partner)

    def test_whatsapp_failed_delivery_marks_the_message(self):
        conv = self.Conversation.create({'account_id': self.whatsapp.id, 'external_user_id': '849', 'name': 'W'})
        message = self.env['omnichat.message'].create({'conversation_id': conv.id, 'direction': 'out',
                                                       'author_kind': 'agent', 'body': 'x', 'state': 'sent',
                                                       'external_id': 'out1'})
        self.receive(self.whatsapp, [providers.event('status', msg_id='out1', payload='failed', text='Expired')])
        self.assertEqual((message.state, message.error), ('failed', 'Expired'))

    def test_done_conversation_reopens(self):
        with self.network() as net:
            self.zalo_ok(net)
            self.receive(self.zalo, [self.zalo_text(msg_id='m1')])
            conv = self.Conversation.search([('account_id', '=', self.zalo.id)])
            conv.action_set_state('done')
            self.assertEqual(conv.unread_count, 0)
            self.receive(self.zalo, [self.zalo_text(msg_id='m2', text='Cho hỏi thêm')])
        self.assertEqual(conv.state, 'new')

    # ------------------------------------------------------------- sending
    def test_agent_sends_text_and_images(self):
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'A'})
        image = self.env['ir.attachment'].with_user(self.agent).create({
            'name': 'catalog.png', 'raw': PNG, 'mimetype': 'image/png',
            'res_model': 'omnichat.conversation', 'res_id': conv.id})
        with self.network() as net:
            self.zalo_ok(net)
            net.on('/upload/image', FakeResponse({'error': 0, 'data': {'attachment_id': 'att1'}}))
            result = conv.with_user(self.agent).action_send_message('Dạ em gửi catalog ạ', [image.id])
        self.assertEqual([m['state'] for m in result], ['sent', 'sent'])
        self.assertEqual([m['type'] for m in result], ['text', 'image'])
        sent = [c['json']['message'] for c in net.sent('/message/cs')]
        self.assertEqual(sent[0], {'text': 'Dạ em gửi catalog ạ'})
        self.assertEqual(sent[1]['attachment']['payload']['elements'][0]['attachment_id'], 'att1')
        self.assertEqual((conv.user_id, conv.state, conv.last_direction), (self.agent, 'open', 'out'))

    def test_failed_send_is_kept_and_can_be_resent(self):
        conv = self.Conversation.create({'account_id': self.facebook.id, 'external_user_id': 'psid', 'name': 'B'})
        with self.network() as net:
            net.on('/me/messages', FakeResponse({'error': {'code': 10, 'message': 'Outside of allowed window'}}))
            [result] = conv.with_user(self.agent).action_send_message('Late reply')
            self.assertEqual(result['state'], 'failed')
            self.assertIn('Outside of allowed window', result['error'])
            net.on('/me/messages', FakeResponse({'message_id': 'mid.ok'}))
            again = self.env['omnichat.message'].browse(result['id']).with_user(self.agent).action_resend()
        self.assertEqual(again['state'], 'sent')

    def test_agent_cannot_send_someone_elses_file(self):
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'A'})
        secret = self.env['ir.attachment'].create({'name': 'payroll.pdf', 'raw': b'%PDF', 'mimetype': 'application/pdf'})
        with self.assertRaises(UserError):  # AccessError is a UserError too
            conv.with_user(self.agent).action_send_message('', [secret.id])

    # ---------------------------------------------------------- AI + handoff
    def ai_account(self):
        self.telegram.write({'ai_enabled': True, 'handoff_label': 'Gặp nhân viên',
                             'handoff_message': 'Dạ, nhân viên sẽ trả lời ngay ạ.', 'away_message': 'Shop sẽ trả lời sớm.'})
        return self.telegram

    def tg_ok(self, net):
        counter = {'n': 0}

        def reply(*args, **kwargs):
            counter['n'] += 1
            return FakeResponse({'ok': True, 'result': {'message_id': counter['n']}})
        net.on('sendMessage', reply)

    def test_ai_answers_with_a_handoff_button(self):
        account = self.ai_account()
        with patch.object(type(self.env['omnichat.account']), '_compute_ai_available',
                          lambda self: self.update({'ai_available': True})), \
                patch.object(OmnichatConversation, '_omni_ai_answer', return_value='Dạ giá **150k** ạ'), \
                self.network() as net:
            self.tg_ok(net)
            self.receive(account, [providers.event('message', user_id='777', name='Khánh', text='Giá bao nhiêu?',
                                                   msg_id='777:1')])
            conv = self.Conversation.search([('account_id', '=', account.id)])
            self.assertEqual(conv.mode, 'ai')
            bot = conv.message_ids.filtered(lambda m: m.author_kind == 'bot')
            self.assertEqual((bot.body, bot.state), ('Dạ giá **150k** ạ', 'sent'))
            body = net.sent('sendMessage')[0]['json']
            self.assertEqual(body['reply_markup']['inline_keyboard'][0][0]['text'], 'Gặp nhân viên')

            # The customer taps the button: a person takes over and AI stops
            self.receive(account, [providers.event('postback', user_id='777', payload=HANDOFF_PAYLOAD, msg_id='cb:1')])
            self.assertEqual(conv.mode, 'human')
            self.assertIn('Dạ, nhân viên sẽ trả lời ngay ạ.', conv.message_ids.mapped('body'))
            calls = len(net.sent('sendMessage'))
            self.receive(account, [providers.event('message', user_id='777', text='Alo', msg_id='777:3')])
            self.assertEqual(len(net.sent('sendMessage')), calls, 'AI no longer answers')

            # Done: the next question goes back to AI
            conv.action_set_state('done')
            self.receive(account, [providers.event('message', user_id='777', text='Còn hàng không?', msg_id='777:4')])
            self.assertEqual(conv.mode, 'ai')

    def test_typed_handoff_and_agent_reply_take_over(self):
        account = self.ai_account()
        with patch.object(type(self.env['omnichat.account']), '_compute_ai_available',
                          lambda self: self.update({'ai_available': True})), \
                patch.object(OmnichatConversation, '_omni_ai_answer', return_value='AI answer'), \
                self.network() as net:
            self.tg_ok(net)
            self.receive(account, [providers.event('message', user_id='1', name='A', text='cho em gặp nhân viên',
                                                   msg_id='1:1')])
            conv = self.Conversation.search([('account_id', '=', account.id), ('external_user_id', '=', '1')])
            self.assertEqual(conv.mode, 'human')
            self.receive(account, [providers.event('message', user_id='2', name='B', text='Hỏi giá', msg_id='2:1')])
            conv2 = self.Conversation.search([('account_id', '=', account.id), ('external_user_id', '=', '2')])
            self.assertEqual(conv2.mode, 'ai')
            conv2.with_user(self.agent).action_send_message('Em là nhân viên ạ')
            self.assertEqual(conv2.mode, 'human')

    def test_ai_failure_hands_over_with_the_away_message(self):
        account = self.ai_account()
        with patch.object(type(self.env['omnichat.account']), '_compute_ai_available',
                          lambda self: self.update({'ai_available': True})), \
                patch.object(OmnichatConversation, '_omni_ai_answer', side_effect=RuntimeError('LLM down')), \
                self.network() as net:
            self.tg_ok(net)
            self.receive(account, [providers.event('message', user_id='9', name='C', text='Hello', msg_id='9:1')])
        conv = self.Conversation.search([('account_id', '=', account.id)])
        self.assertEqual(conv.mode, 'human')
        self.assertEqual(conv.message_ids.filtered(lambda m: m.direction == 'out').mapped('body'), ['Shop sẽ trả lời sớm.'])
        self.assertTrue(conv.message_ids.filtered(lambda m: m.direction == 'note'))

    def test_photo_goes_to_a_person(self):
        account = self.ai_account()
        with patch.object(type(self.env['omnichat.account']), '_compute_ai_available',
                          lambda self: self.update({'ai_available': True})), \
                patch.object(OmnichatConversation, '_omni_ai_answer', return_value='x') as answer, \
                self.network() as net:
            self.tg_ok(net)
            self.receive(account, [providers.event('message', user_id='5', name='P', msg_id='5:1',
                                                   attachments=[{'type': 'image', 'media_id': 'f'}])])
            answer.assert_not_called()
        self.assertEqual(self.Conversation.search([('account_id', '=', account.id)]).mode, 'human')

    def test_ai_chatbot_bridge(self):
        if 'isd.ai.chatbot.conversation' not in self.env:
            self.skipTest('AI Chatbot is not installed')
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'ai1', 'name': 'A'})
        answer = conv._omni_ai_answer('Xin chào')
        self.assertTrue(answer)
        self.assertTrue(self.env['isd.ai.chatbot.conversation'].search([('session_id', '=', 'omnichat:%s' % conv.id)]))

    def test_initials(self):
        from odoo.addons.isd_omnichat.models.account import _initials
        self.assertEqual(_initials('Chị Thảo Linh Boutique'), 'TL')
        self.assertEqual(_initials('Anh Minh Hoàng (Đại lý VIP)'), 'MH')
        self.assertEqual(_initials('Đức Khánh • Kho Hà Nội'), 'ĐK')
        self.assertEqual(_initials('ngocphuong.shop'), 'NG')
        self.assertEqual(_initials(''), '?')

    def test_strip_markdown(self):
        self.assertEqual(strip_markdown('**Giá:** 150k\n* Màu đỏ\nXem [1](https://x.vn/a)'),
                         'Giá: 150k\n• Màu đỏ\nXem 1 (https://x.vn/a)')

    # --------------------------------------------------------------- credit
    def test_credit_per_messages_and_ai_handover_when_empty(self):
        if 'isd.credit.rule' not in self.env:
            self.skipTest('Credit is not installed')
        self.env['isd.credit.rule'].create({
            'name': 'Tin nhắn gửi đi', 'module_id': self.env.ref('base.module_base').id, 'operation': 'create',
            'model_id': self.env.ref('isd_omnichat.model_omnichat_message').id, 'credits': 1, 'per_qty': 2,
            'domain': "[('direction', '=', 'out')]",
        })
        wallet = self.env['isd.credit.wallet']._get_wallet()
        wallet.sudo().write({'system_balance': 1, 'topup_balance': 0})
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'A'})
        with self.network() as net:
            self.zalo_ok(net)
            conv.with_user(self.agent).action_send_message('1')
            self.assertEqual(wallet.balance, 1)
            conv.with_user(self.agent).action_send_message('2')
            self.assertEqual(wallet.balance, 0, '1 credit per 2 messages')
            with self.assertRaisesRegex(UserError, 'Not enough credit'):
                conv.with_user(self.agent).action_send_message('3')
            # Customers still get through, and AI hands over instead of failing
            account = self.ai_account()
            with patch.object(type(self.env['omnichat.account']), '_compute_ai_available',
                              lambda self: self.update({'ai_available': True})), \
                    patch.object(OmnichatConversation, '_omni_ai_answer', return_value='AI'):
                self.tg_ok(net)
                public = self.env.ref('base.public_user')
                self.Conversation.with_user(public).sudo()._omni_receive(
                    account, [providers.event('message', user_id='c1', name='C', text='Hi', msg_id='c1:1')])
            tg = self.Conversation.search([('account_id', '=', account.id)])
            self.assertEqual(tg.message_ids.filtered(lambda m: m.direction == 'in').body, 'Hi')
            self.assertEqual(tg.mode, 'human')

    # --------------------------------------------------------------- access
    def test_agents_see_only_their_accounts(self):
        mine = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'Mine'})
        other = self.Conversation.create({'account_id': self.shopee.id, 'external_user_id': '1', 'name': 'Other'})
        Conv = self.Conversation.with_user(self.agent)
        self.assertEqual(Conv.search([('id', 'in', (mine | other).ids)]), mine)
        accounts = {a['id'] for a in self.env['omnichat.account'].with_user(self.agent).get_inbox_accounts()}
        self.assertIn(self.zalo.id, accounts)
        self.assertNotIn(self.shopee.id, accounts)
        with self.assertRaises(AccessError):
            other.with_user(self.agent).get_thread()
        with self.assertRaises(AccessError):
            self.zalo.with_user(self.agent).read(['access_token'])
        with self.assertRaises(AccessError):
            self.zalo.with_user(self.agent).write({'name': 'x'})

    def test_inbox_list_filters_and_thread(self):
        with self.network() as net:
            self.zalo_ok(net)
            self.receive(self.zalo, [self.zalo_text(user='u1', msg_id='a'), self.zalo_text(user='u2', msg_id='b')])
        Conv = self.Conversation.with_user(self.agent)
        items = Conv.get_inbox_list(account_id=self.zalo.id)['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(len(Conv.get_inbox_list(filter='unread')['items']), 2)
        conv = Conv.browse(items[0]['id'])
        thread = conv.get_thread()
        self.assertEqual(len(thread['messages']), 1)
        self.assertEqual(conv.unread_count, 0, 'opening reads it')
        self.assertEqual(len(Conv.get_inbox_list(filter='unread')['items']), 1)
        self.assertEqual(Conv.get_unread_total(), 1)
        self.assertEqual(len(Conv.get_inbox_list(search='Minh')['items']), 2)
        tag = conv.action_create_tag('VIP')
        self.assertEqual(Conv.get_inbox_list(search='VIP')['items'][0]['tags'][0]['id'], tag['id'])

    def test_link_contact(self):
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'Lan Anh',
                                         'phone': '0912345678'})
        conv.with_user(self.agent).action_link_partner()
        self.assertEqual((conv.partner_id.name, conv.partner_id.phone), ('Lan Anh', '0912345678'))
        self.assertEqual(conv.partner_id.omnichat_conversation_count, 1)

    # -------------------------------------------------------- quick replies
    def test_quick_replies(self):
        QR = self.env['omnichat.quick.reply']
        QR.with_context(active_test=False).search([]).unlink()
        images = self.env['ir.attachment'].create([{'name': 'i%s.png' % i, 'raw': PNG} for i in range(7)])
        shared = QR.create({'shortcut': '\\xinchao', 'body': 'Dạ chào {name}', 'user_id': False,
                            'image_ids': [(6, 0, images[:2].ids)]})
        self.assertEqual(shared.shortcut, 'xinchao')
        self.assertEqual(images[0].res_model, 'omnichat.quick.reply')
        with self.assertRaises(ValidationError):
            QR.create({'shortcut': 'too many', 'body': 'x', 'user_id': False})
        with self.assertRaises(ValidationError):
            QR.create({'shortcut': 'xinchao', 'body': 'again', 'user_id': False})
        with self.assertRaises(ValidationError):
            QR.create({'shortcut': 'anh', 'body': 'x', 'user_id': False, 'image_ids': [(6, 0, images.ids)]})
        mine = QR.with_user(self.agent).save_from_inbox(False, 'xinchao', 'Chào riêng', [])
        self.assertTrue(mine['mine'])
        replies = QR.with_user(self.agent).get_for_inbox()
        self.assertEqual([(r['shortcut'], r['body']) for r in replies], [('xinchao', 'Chào riêng')],
                         'the agent\'s own reply wins over the shared one')
        with self.assertRaises(AccessError):
            shared.with_user(self.agent).write({'body': 'changed'})
        # The shared reply's images can be sent by an agent
        conv = self.Conversation.create({'account_id': self.zalo.id, 'external_user_id': 'u1', 'name': 'A'})
        with self.network() as net:
            self.zalo_ok(net)
            net.on('/upload/image', FakeResponse({'error': 0, 'data': {'attachment_id': 'z'}}))
            result = conv.with_user(self.agent).action_send_message('', images[:1].ids)
        self.assertEqual(result[0]['state'], 'sent')


@tagged('post_install', '-at_install')
class TestWebhook(HttpCase):

    def setUp(self):
        super().setUp()
        self.account = self.env['omnichat.account'].create({
            'name': 'Zalo', 'platform': 'zalo', 'app_id': 'zapp', 'webhook_secret': 'oa-secret',
            'access_token': 't'})

    def post(self, body, signature):
        return self.url_open('/omnichat/webhook/zalo/%s' % self.account.webhook_token, data=body,
                             headers={'Content-Type': 'application/json', 'X-ZEvent-Signature': 'mac=' + signature})

    def test_webhook_checks_signature_and_stores(self):
        payload = {'app_id': 'zapp', 'event_name': 'user_send_text', 'timestamp': '1732300000000',
                   'sender': {'id': 'u9'}, 'recipient': {'id': 'oa'}, 'message': {'msg_id': 'w1', 'text': 'Hello'}}
        body = json.dumps(payload)
        good = hashlib.sha256(('zapp' + body + '1732300000000' + 'oa-secret').encode()).hexdigest()
        with patch.object(providers, '_http', lambda *a, **k: FakeResponse({'error': 0, 'data': {}})):
            self.assertEqual(self.post(body, 'forged').status_code, 200)
            self.assertFalse(self.env['omnichat.conversation'].search([('account_id', '=', self.account.id)]))
            self.assertEqual(self.post(body, good).status_code, 200)
        conv = self.env['omnichat.conversation'].search([('account_id', '=', self.account.id)])
        self.assertEqual(conv.message_ids.body, 'Hello')
        logs = self.env['omnichat.webhook.log'].search([('account_id', '=', self.account.id)])
        self.assertEqual(sorted(logs.mapped('status')), ['processed', 'rejected'])
        self.assertEqual(self.url_open('/omnichat/webhook/zalo/wrong-token', data='{}').status_code, 404)

    def test_facebook_subscription_check(self):
        fb = self.env['omnichat.account'].create({'name': 'FB', 'platform': 'facebook', 'webhook_secret': 'tok'})
        res = self.url_open('/omnichat/webhook/facebook/%s?hub.mode=subscribe&hub.verify_token=tok&hub.challenge=99'
                            % fb.webhook_token)
        self.assertEqual((res.status_code, res.text), (200, '99'))
