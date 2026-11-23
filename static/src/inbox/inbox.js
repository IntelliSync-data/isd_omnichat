/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { OmnichatSettingsDialog } from "./settings_dialog";
import { readFiles } from "./files";

const { DateTime } = luxon;
const SHORTCUT_RE = /(^|\s)\\([\p{L}\p{N}_-]*)$/u;

/** The unified inbox: accounts on the left, their conversations, and the chat. */
export class OmnichatInbox extends Component {
    static template = "isd_omnichat.Inbox";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.bus = useService("bus_service");
        this.omnichat = useState(useService("isd_omnichat").state);
        this.omnichatService = useService("isd_omnichat");
        this.composerRef = useRef("composer");
        this.threadRef = useRef("thread");
        this.fileRef = useRef("file");
        const params = this.props.action?.params || {};
        this.state = useState({
            accounts: [],
            accountId: params.account_id || false,
            filter: "active",
            search: "",
            conversations: [],
            hasMore: false,
            convId: params.conversation_id || false,
            conv: null,
            messages: [],
            hasMoreMessages: false,
            loadingThread: false,
            quickReplies: [],
            composer: "",
            pending: [],
            qr: { open: false, query: "", index: 0 },
            sending: false,
            uploading: false,
            tagInput: "",
            showTagMenu: false,
            showAssignMenu: false,
        });
        this.isManager = false;
        this.userName = user.name;
        this.onBus = this.onBus.bind(this);

        onWillStart(async () => {
            this.isManager = await user.hasGroup("isd_omnichat.group_omnichat_manager");
            await Promise.all([this.loadAccounts(), this.loadConversations(), this.loadQuickReplies()]);
            if (this.state.convId) {
                await this.openConversation(this.state.convId);
            }
        });
        onMounted(() => this.bus.subscribe("isd_omnichat/update", this.onBus));
        onWillUnmount(() => {
            this.bus.unsubscribe("isd_omnichat/update", this.onBus);
            clearTimeout(this.refreshTimer);
            clearTimeout(this.searchTimer);
        });
    }

    // ------------------------------------------------------------- loading
    async loadAccounts() {
        this.state.accounts = await this.orm.call("omnichat.account", "get_inbox_accounts", []);
    }

    async loadConversations(append = false) {
        const result = await this.orm.call("omnichat.conversation", "get_inbox_list", [], {
            account_id: this.state.accountId || false,
            filter: this.state.filter,
            search: this.state.search,
            offset: append ? this.state.conversations.length : 0,
        });
        this.state.conversations = append ? [...this.state.conversations, ...result.items] : result.items;
        this.state.hasMore = result.has_more;
    }

    async loadQuickReplies() {
        this.state.quickReplies = await this.orm.call("omnichat.quick.reply", "get_for_inbox", []);
    }

    async openConversation(convId) {
        this.state.convId = convId;
        this.state.loadingThread = true;
        this.state.pending = [];
        this.state.showTagMenu = false;
        this.state.showAssignMenu = false;
        try {
            const result = await this.orm.call("omnichat.conversation", "get_thread", [[convId]]);
            this.state.conv = result.conversation;
            this.state.messages = result.messages;
            this.state.hasMoreMessages = result.has_more;
            const item = this.state.conversations.find((c) => c.id === convId);
            if (item) {
                item.unread = 0;
            }
        } finally {
            this.state.loadingThread = false;
        }
        this.scrollToBottom();
        setTimeout(() => this.composerRef.el?.focus(), 50);
    }

    async loadOlder() {
        const first = this.state.messages[0];
        if (!first) {
            return;
        }
        const el = this.threadRef.el;
        const before = el ? el.scrollHeight - el.scrollTop : 0;
        const result = await this.orm.call("omnichat.conversation", "get_thread", [[this.state.convId]], {
            before_id: first.id,
        });
        this.state.messages = [...result.messages, ...this.state.messages];
        this.state.hasMoreMessages = result.has_more;
        if (el) {
            requestAnimationFrame(() => (el.scrollTop = el.scrollHeight - before));
        }
    }

    async fetchNewMessages() {
        const last = this.state.messages[this.state.messages.length - 1];
        const result = await this.orm.call("omnichat.conversation", "get_thread", [[this.state.convId]], {
            after_id: last ? last.id : false,
        });
        const known = new Set(this.state.messages.map((m) => m.id));
        const fresh = result.messages.filter((m) => !known.has(m.id));
        // Messages whose state changed (a failed delivery) come back too
        for (const message of result.messages) {
            const idx = this.state.messages.findIndex((m) => m.id === message.id);
            if (idx >= 0) {
                this.state.messages[idx] = message;
            }
        }
        this.state.conv = result.conversation;
        if (fresh.length) {
            this.state.messages.push(...fresh);
            this.scrollToBottom();
        }
    }

    onBus(payload) {
        clearTimeout(this.refreshTimer);
        this.refreshTimer = setTimeout(async () => {
            await Promise.all([this.loadAccounts(), this.loadConversations()]);
            if (this.state.convId && payload.conversation_id === this.state.convId) {
                await this.fetchNewMessages();
            }
        }, 250);
    }

    scrollToBottom() {
        requestAnimationFrame(() => {
            const el = this.threadRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }

    // ----------------------------------------------------------- left side
    selectAccount(accountId) {
        this.state.accountId = accountId;
        this.loadConversations();
    }

    setFilter(filter) {
        this.state.filter = filter;
        this.loadConversations();
    }

    onSearch(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.loadConversations(), 300);
    }

    backToList() {
        this.state.convId = false;
        this.state.conv = null;
    }

    get currentAccount() {
        return this.state.accounts.find((a) => a.id === this.state.accountId);
    }

    get unreadTotal() {
        return this.state.accounts.reduce((sum, a) => sum + a.unread, 0);
    }

    get filters() {
        const filters = [
            ["active", _t("Open")],
            ["unread", _t("Unread")],
            ["mine", _t("Mine")],
        ];
        if (this.state.accounts.some((a) => a.ai)) {
            filters.push(["ai", _t("AI")]);
        }
        filters.push(["done", _t("Done")]);
        return filters;
    }

    accountAvatar(account) {
        return `/web/image/omnichat.account/${account.id}/image_128`;
    }

    convAvatar(conv) {
        return `/web/image/omnichat.conversation/${conv.id}/image_128`;
    }

    // -------------------------------------------------------------- formats
    toLocal(value) {
        return value ? deserializeDateTime(value) : null;
    }

    listTime(value) {
        const dt = this.toLocal(value);
        if (!dt) {
            return "";
        }
        const now = DateTime.local();
        if (dt.hasSame(now, "day")) {
            return dt.toFormat("HH:mm");
        }
        if (dt.hasSame(now, "year")) {
            return dt.toFormat("dd/MM");
        }
        return dt.toFormat("dd/MM/yy");
    }

    messageTime(value) {
        const dt = this.toLocal(value);
        return dt ? dt.toFormat("HH:mm") : "";
    }

    dayLabel(message, index) {
        const dt = this.toLocal(message.date);
        if (!dt) {
            return "";
        }
        const prev = index > 0 ? this.toLocal(this.state.messages[index - 1].date) : null;
        if (prev && prev.hasSame(dt, "day")) {
            return "";
        }
        const now = DateTime.local();
        if (dt.hasSame(now, "day")) {
            return _t("Today");
        }
        if (dt.hasSame(now.minus({ days: 1 }), "day")) {
            return _t("Yesterday");
        }
        return dt.toFormat("dd/MM/yyyy");
    }

    // ---------------------------------------------------- quick replies (\)
    get qrMatches() {
        const query = this.state.qr.query.toLowerCase();
        const replies = this.state.quickReplies;
        const starts = replies.filter((r) => r.shortcut.toLowerCase().startsWith(query));
        const contains = replies.filter(
            (r) =>
                !r.shortcut.toLowerCase().startsWith(query) &&
                (r.shortcut.toLowerCase().includes(query) || r.body.toLowerCase().includes(query))
        );
        return [...starts, ...contains].slice(0, 8);
    }

    onComposerInput(ev) {
        this.state.composer = ev.target.value;
        const before = ev.target.value.slice(0, ev.target.selectionStart);
        const match = before.match(SHORTCUT_RE);
        if (match && this.state.quickReplies.length) {
            this.state.qr.open = true;
            this.state.qr.query = match[2];
            this.state.qr.index = 0;
        } else {
            this.state.qr.open = false;
        }
        this.autoGrow(ev.target);
    }

    onComposerBlur() {
        setTimeout(() => (this.state.qr.open = false), 150);
    }

    autoGrow(el) {
        el.style.height = "auto";
        el.style.height = Math.min(el.scrollHeight, 160) + "px";
    }

    onComposerKeydown(ev) {
        const qr = this.state.qr;
        const matches = qr.open ? this.qrMatches : [];
        if (qr.open && matches.length) {
            if (ev.key === "ArrowDown") {
                ev.preventDefault();
                qr.index = (qr.index + 1) % matches.length;
                return;
            }
            if (ev.key === "ArrowUp") {
                ev.preventDefault();
                qr.index = (qr.index - 1 + matches.length) % matches.length;
                return;
            }
            if (ev.key === "Enter" || ev.key === "Tab") {
                ev.preventDefault();
                this.applyQuickReply(matches[qr.index]);
                return;
            }
        }
        if (ev.key === "Escape" && qr.open) {
            qr.open = false;
            return;
        }
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            this.send();
        }
    }

    fillTemplate(text) {
        const name = this.state.conv?.name || "";
        return text.replaceAll("{name}", name).replaceAll("{agent}", this.userName || "");
    }

    applyQuickReply(reply) {
        const el = this.composerRef.el;
        const value = this.state.composer;
        const caret = el ? el.selectionStart : value.length;
        const before = value.slice(0, caret).replace(SHORTCUT_RE, (m, space) => space);
        const body = this.fillTemplate(reply.body);
        this.state.composer = before + body + value.slice(caret);
        for (const image of reply.images) {
            if (!this.state.pending.some((p) => p.id === image.id)) {
                this.state.pending.push({ id: image.id, name: image.name, is_image: true, url: image.url });
            }
        }
        this.state.qr.open = false;
        requestAnimationFrame(() => {
            if (el) {
                el.value = this.state.composer;
                const pos = (before + body).length;
                el.focus();
                el.setSelectionRange(pos, pos);
                this.autoGrow(el);
            }
        });
    }

    // ------------------------------------------------------------- sending
    pickFiles() {
        this.fileRef.el?.click();
    }

    async onFiles(ev) {
        const files = ev.target.files;
        if (!files?.length) {
            return;
        }
        this.state.uploading = true;
        try {
            const data = await readFiles(files);
            const ids = await this.orm.create(
                "ir.attachment",
                data.map((f) => ({ ...f, res_model: "omnichat.conversation", res_id: this.state.convId }))
            );
            ids.forEach((id, i) => {
                const isImage = (data[i].mimetype || "").startsWith("image/");
                this.state.pending.push({
                    id,
                    name: data[i].name,
                    is_image: isImage,
                    url: `/web/image/${id}/128x128`,
                });
            });
        } finally {
            this.state.uploading = false;
            ev.target.value = "";
        }
    }

    removePending(id) {
        this.state.pending = this.state.pending.filter((p) => p.id !== id);
    }

    async send() {
        const body = this.state.composer.trim();
        if ((!body && !this.state.pending.length) || this.state.sending || !this.state.convId) {
            return;
        }
        this.state.sending = true;
        try {
            const messages = await this.orm.call(
                "omnichat.conversation",
                "action_send_message",
                [[this.state.convId]],
                { body, attachment_ids: this.state.pending.map((p) => p.id) }
            );
            this.state.composer = "";
            this.state.pending = [];
            if (this.composerRef.el) {
                this.composerRef.el.value = "";
                this.autoGrow(this.composerRef.el);
            }
            const known = new Set(this.state.messages.map((m) => m.id));
            this.state.messages.push(...messages.filter((m) => !known.has(m.id)));
            const failed = messages.filter((m) => m.state === "failed");
            if (failed.length) {
                this.notification.add(failed[0].error, { type: "danger", title: _t("Not sent") });
            }
            this.scrollToBottom();
            await this.fetchNewMessages();
            this.loadConversations();
        } finally {
            this.state.sending = false;
        }
    }

    async resend(message) {
        const data = await this.orm.call("omnichat.message", "action_resend", [[message.id]]);
        const idx = this.state.messages.findIndex((m) => m.id === message.id);
        if (idx >= 0) {
            this.state.messages[idx] = data;
        }
        if (data.state === "failed") {
            this.notification.add(data.error, { type: "danger", title: _t("Still not sent") });
        }
    }

    // ------------------------------------------------------ header actions
    async callConv(method, args = [], kwargs = {}) {
        const result = await this.orm.call("omnichat.conversation", method, [[this.state.convId], ...args], kwargs);
        await this.fetchNewMessages();
        this.loadConversations();
        return result;
    }

    setState(state) {
        return this.callConv("action_set_state", [state]);
    }

    toggleMode() {
        return this.callConv("action_set_mode", [this.state.conv.mode === "ai" ? "human" : "ai"]);
    }

    async assign(userId) {
        this.state.showAssignMenu = false;
        await this.callConv("action_assign", [userId]);
    }

    hasTag(tag) {
        return this.state.conv.tags.some((t) => t.id === tag.id);
    }

    async toggleTag(tag) {
        const ids = this.state.conv.tags.map((t) => t.id);
        const next = this.hasTag(tag) ? ids.filter((id) => id !== tag.id) : [...ids, tag.id];
        await this.callConv("action_set_tags", [next]);
    }

    async addTag(ev) {
        if (ev.key !== "Enter" || !this.state.tagInput.trim()) {
            return;
        }
        await this.callConv("action_create_tag", [this.state.tagInput.trim()]);
        this.state.tagInput = "";
    }

    async openAction(method) {
        const action = await this.orm.call("omnichat.conversation", method, [[this.state.convId]]);
        await this.action.doAction(action);
    }

    rename() {
        const name = window.prompt(_t("Customer name"), this.state.conv.name);
        if (name) {
            this.callConv("action_rename", [name]);
        }
    }

    openSellerCenter() {
        window.open(this.state.conv?.seller_center_url || this.currentAccount?.seller_center_url, "_blank");
    }

    // -------------------------------------------------------------- settings
    openSettings() {
        const reload = () => Promise.all([this.loadAccounts(), this.loadQuickReplies(), this.loadConversations()]);
        this.dialog.add(OmnichatSettingsDialog, { isManager: this.isManager, onChanged: reload }, { onClose: reload });
    }

    toggleSound() {
        this.omnichatService.setPreferences({ sound: !this.omnichat.sound });
        if (!this.omnichat.sound) {
            this.omnichatService.chime();
        }
    }

    toggleDesktop() {
        this.omnichatService.setPreferences({ desktop: !this.omnichat.desktop });
    }

    confirmDone() {
        this.dialog.add(ConfirmationDialog, {
            body: _t("Mark this conversation as done? The customer's next message opens it again."),
            confirm: () => this.setState("done"),
            cancel: () => {},
        });
    }
}

registry.category("actions").add("isd_omnichat.inbox", OmnichatInbox);
