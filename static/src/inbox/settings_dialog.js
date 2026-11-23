/** @odoo-module **/
import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { readFiles } from "./files";

const MAX_IMAGES = 6;
const MAX_SHORTCUT = 20;

/** Settings, as in the desktop app: the accounts (managers) and the quick replies. */
export class OmnichatSettingsDialog extends Component {
    static template = "isd_omnichat.SettingsDialog";
    static components = { Dialog };
    static props = ["isManager", "onChanged", "close"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.fileRef = useRef("qrFile");
        this.state = useState({
            tab: this.props.isManager ? "accounts" : "replies",
            groups: [],
            replies: [],
            form: this.emptyForm(),
            saving: false,
        });
        this.maxImages = MAX_IMAGES;
        this.maxShortcut = MAX_SHORTCUT;
        this.changed = false;
        onWillStart(() => Promise.all([this.props.isManager ? this.loadAccounts() : null, this.loadReplies()]));
    }

    get title() {
        return _t("Omnichat Settings");
    }

    emptyForm() {
        return { id: false, shortcut: "", body: "", images: [], shared: this.props.isManager };
    }

    async loadAccounts() {
        this.state.groups = await this.orm.call("omnichat.account", "get_settings_accounts", []);
    }

    async loadReplies() {
        this.state.replies = await this.orm.call("omnichat.quick.reply", "get_for_inbox", []);
    }

    markChanged() {
        this.changed = true;
    }

    close() {
        this.props.close();
    }

    // -------------------------------------------------------------- accounts
    accountAvatar(account) {
        return `/web/image/omnichat.account/${account.id}/image_128`;
    }

    async editAccount(accountId = false) {
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "omnichat.account",
                res_id: accountId || undefined,
                views: [[false, "form"]],
                target: "new",
                name: accountId ? _t("Channel Account") : _t("New Channel Account"),
            },
            {
                onClose: async () => {
                    this.markChanged();
                    await this.loadAccounts();
                },
            }
        );
    }

    async toggleHidden(account) {
        await this.orm.call("omnichat.account", "action_toggle_hidden", [[account.id]]);
        this.markChanged();
        await this.loadAccounts();
    }

    deleteAccount(account) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete %s?", account.name),
            body: _t("Its conversations and messages are deleted too. To keep them, hide or archive the account instead."),
            confirmLabel: _t("Delete"),
            confirm: async () => {
                await this.orm.unlink("omnichat.account", [account.id]);
                this.markChanged();
                await this.loadAccounts();
            },
            cancel: () => {},
        });
    }

    openSellerCenter(account) {
        window.open(account.seller_center_url, "_blank");
    }

    // --------------------------------------------------------- quick replies
    onShortcutInput(ev) {
        const value = ev.target.value.replace(/^\\+/, "").replace(/\s+/g, "").slice(0, MAX_SHORTCUT);
        this.state.form.shortcut = value;
        ev.target.value = value;
    }

    pickImages() {
        this.fileRef.el?.click();
    }

    async onImages(ev) {
        const room = MAX_IMAGES - this.state.form.images.length;
        const files = [...(ev.target.files || [])].filter((f) => f.type.startsWith("image/")).slice(0, room);
        ev.target.value = "";
        if (!files.length) {
            return;
        }
        const data = await readFiles(files);
        const ids = await this.orm.create("ir.attachment", data);
        ids.forEach((id, i) =>
            this.state.form.images.push({ id, name: data[i].name, url: `/web/image/${id}/128x128` })
        );
    }

    removeImage(id) {
        this.state.form.images = this.state.form.images.filter((img) => img.id !== id);
    }

    editReply(reply) {
        this.state.form = {
            id: reply.id,
            shortcut: reply.shortcut,
            body: reply.body,
            images: [...reply.images],
            shared: reply.shared,
        };
    }

    cancelReply() {
        this.state.form = this.emptyForm();
    }

    async saveReply() {
        const form = this.state.form;
        if (!form.shortcut || !form.body.trim()) {
            this.notification.add(_t("Enter a shortcut and a message."), { type: "warning" });
            return;
        }
        this.state.saving = true;
        try {
            await this.orm.call("omnichat.quick.reply", "save_from_inbox", [
                form.id,
                form.shortcut,
                form.body,
                form.images.map((img) => img.id),
                form.shared,
            ]);
            this.state.form = this.emptyForm();
            this.markChanged();
            await this.loadReplies();
        } finally {
            this.state.saving = false;
        }
    }

    canEdit(reply) {
        return this.props.isManager || reply.mine;
    }

    deleteReply(reply) {
        this.dialog.add(ConfirmationDialog, {
            body: _t("Delete the quick reply \\%s?", reply.shortcut),
            confirmLabel: _t("Delete"),
            confirm: async () => {
                await this.orm.unlink("omnichat.quick.reply", [reply.id]);
                this.markChanged();
                await this.loadReplies();
            },
            cancel: () => {},
        });
    }
}
