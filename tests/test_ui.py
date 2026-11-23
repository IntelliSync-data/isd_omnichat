# -*- coding: utf-8 -*-
from odoo.tests import HttpCase, tagged

WAIT = """
const wait = (selector) => new Promise((resolve, reject) => {
    let tries = 0;
    const timer = setInterval(() => {
        const el = document.querySelector(selector);
        if (el) {
            clearInterval(timer);
            resolve(el);
        } else if (++tries > 150) {
            clearInterval(timer);
            reject(new Error('missing ' + selector));
        }
    }, 100);
});
"""


@tagged('post_install', '-at_install')
class TestInboxUi(HttpCase):

    def test_inbox_quick_reply_popup(self):
        account = self.env['omnichat.account'].create({
            'name': 'Zalo Kinh Doanh 1', 'platform': 'zalo', 'access_token': 't',
            'user_ids': [(4, self.env.ref('base.user_admin').id)]})
        self.env['omnichat.conversation'].create({
            'account_id': account.id, 'external_user_id': 'u1', 'name': 'Anh Minh Hoàng',
            'last_message_preview': 'Gửi giúp anh bảng giá', 'unread_count': 2})
        self.env['omnichat.quick.reply'].with_context(active_test=False).search([]).unlink()
        self.env['omnichat.quick.reply'].create({'shortcut': 'xinchao', 'body': 'Dạ chào {name}!', 'user_id': False})
        self.browser_js('/odoo/omnichat', WAIT + """
            (async () => {
                await wait('.o_oc_rail .o_oc_rail_item .o_oc_avatar');
                (await wait('.o_oc_conv')).click();
                const box = await wait('textarea.o_oc_input');
                box.focus();
                box.value = '\\\\xin';
                box.setSelectionRange(4, 4);
                box.dispatchEvent(new Event('input', { bubbles: true }));
                const item = await wait('.o_oc_qr_item');
                if (!item.textContent.includes('xinchao')) throw new Error('quick reply not listed');
                box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                await new Promise((r) => setTimeout(r, 300));
                if (!box.value.includes('Dạ chào Anh Minh Hoàng!')) throw new Error('not inserted: ' + box.value);
                (await wait('.o_oc_rail_tool .fa-th-large')).click();
                await wait('.o_oc_settings .o_oc_account_row');
                console.log('test successful');
            })().catch((e) => console.error(e.message));
        """, login='admin')
