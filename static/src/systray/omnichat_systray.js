/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** The chat bubble in the top bar, with the number of unread conversations. */
export class OmnichatSystray extends Component {
    static template = "isd_omnichat.Systray";
    static props = {};

    setup() {
        this.omnichat = useState(useService("isd_omnichat").state);
        this.action = useService("action");
    }

    open() {
        this.action.doAction("isd_omnichat.action_inbox");
    }
}

registry.category("systray").add("isd_omnichat.Systray", { Component: OmnichatSystray }, { sequence: 25 });
