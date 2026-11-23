# -*- coding: utf-8 -*-
{
    'name': 'Omnichat (by ISD)',
    'version': '18.0.1.0.0',
    'category': 'ISD Modules',
    'summary': 'One inbox for Zalo OA, Facebook pages, WhatsApp, Telegram, Shopee and TikTok Shop, '
               'with quick replies, AI auto-reply and handoff to a person',
    'description': """
Omnichat (by ISD)
=================
* Many accounts per platform: Zalo OA, Facebook page (Messenger), WhatsApp Business,
  Telegram bot, Shopee and TikTok Shop seller chat, each with its own webhook
* One inbox: accounts on the left, conversations, chat; new messages arrive live
  with a sound and a desktop notification
* Quick replies: type \\ in the chat box, pick one, and its text and up to 6 images go out
* Assign conversations, tag them, close them, link the customer to a contact or a CRM lead
* AI auto-reply through AI Chatbot (by ISD) when it is installed; the customer
  taps "Talk to a person" and an agent takes over
* Agents see the accounts they are assigned to, managers see everything
    """,
    'author': 'IntelliSyncData',
    'website': 'https://intellisyncdata.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'bus', 'web', 'contacts'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'views/account_views.xml',
        'views/conversation_views.xml',
        'views/quick_reply_views.xml',
        'views/tag_views.xml',
        'views/webhook_log_views.xml',
        'views/res_partner_views.xml',
        'views/res_users_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'isd_omnichat/static/src/scss/*.scss',
            'isd_omnichat/static/src/inbox/*.js',
            'isd_omnichat/static/src/inbox/*.xml',
            'isd_omnichat/static/src/systray/*.js',
            'isd_omnichat/static/src/systray/*.xml',
        ],
    },
    'installable': True,
    'application': True,
}
