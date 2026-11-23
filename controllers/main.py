# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

from ..services.providers import PROVIDERS

_logger = logging.getLogger(__name__)

MAX_BODY = 2 * 1024 * 1024


class OmnichatWebhook(http.Controller):

    @http.route('/omnichat/webhook/<string:platform>/<string:token>', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, save_session=False)
    def webhook(self, platform, token, **params):
        """Where each platform delivers messages. The token in the address picks the account;
        the platform's signature proves the call is genuine."""
        env = request.env(su=True)
        req = request.httprequest
        raw = req.get_data(cache=True) or b''
        account = env['omnichat.account'].with_context(active_test=False).search(
            [('webhook_token', '=', token), ('platform', '=', platform)], limit=1)
        if platform not in PROVIDERS or not account:
            return request.make_response('', status=404)
        log = env['omnichat.webhook.log'].create({
            'account_id': account.id, 'platform': platform, 'remote_ip': req.remote_addr,
            'payload': raw[:20000].decode('utf-8', errors='replace') if raw else json.dumps(dict(req.args)),
        })
        provider = account._provider()
        if req.method == 'GET':
            answer = provider.handle_get(dict(req.args))
            if answer is None:
                log.write({'status': 'ignored', 'note': 'GET without a subscription check'})
                return request.make_response('ok', status=200)
            status, body = answer
            log.write({'status': 'processed' if status == 200 else 'rejected', 'note': 'subscription check'})
            return request.make_response(body, status=status, headers=[('Content-Type', 'text/plain')])

        if len(raw) > MAX_BODY:
            log.write({'status': 'rejected', 'note': 'body too large'})
            return request.make_response('', status=413)
        if not provider.verify(raw, req.headers, account.webhook_url):
            log.write({'status': 'rejected', 'note': 'bad signature or secret'})
            # 200 so a platform does not keep retrying a call that will never pass
            return request.make_response('', status=200)
        if not account.active:
            log.write({'status': 'ignored', 'note': 'account archived'})
            return request.make_response('', status=200)
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            log.write({'status': 'rejected', 'note': 'not JSON'})
            return request.make_response('', status=200)
        try:
            events = provider.parse(payload)
            if not events:
                log.write({'status': 'ignored', 'note': 'nothing to store'})
                return request.make_response('', status=200)
            note = env['omnichat.conversation']._omni_receive(account, events)
            log.write({'status': 'processed', 'note': note})
        except Exception as error:  # noqa: BLE001 - logged; the platform must still get its 200
            _logger.exception('isd_omnichat: webhook for %s failed', account.name)
            request.env.cr.rollback()
            env['omnichat.webhook.log'].create({
                'account_id': account.id, 'platform': platform, 'status': 'error', 'note': str(error)[:1000],
                'payload': raw[:20000].decode('utf-8', errors='replace'), 'remote_ip': req.remote_addr})
        return request.make_response('', status=200)
