/** @odoo-module **/
import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";

/**
 * What every screen shares: the unread count for the top bar, the sound and
 * desktop-notification preferences, and a chime when a customer writes.
 */
export const omnichatService = {
    dependencies: ["bus_service", "orm", "action"],
    async start(env, { bus_service, orm, action }) {
        const state = reactive({ isAgent: false, unread: 0, sound: true, desktop: true });
        let audio = null;

        function chime() {
            try {
                audio = audio || new (window.AudioContext || window.webkitAudioContext)();
                const now = audio.currentTime;
                [880, 1320].forEach((freq, i) => {
                    const osc = audio.createOscillator();
                    const gain = audio.createGain();
                    osc.type = "sine";
                    osc.frequency.value = freq;
                    gain.gain.setValueAtTime(0.0001, now + i * 0.12);
                    gain.gain.exponentialRampToValueAtTime(0.25, now + i * 0.12 + 0.02);
                    gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 0.35);
                    osc.connect(gain).connect(audio.destination);
                    osc.start(now + i * 0.12);
                    osc.stop(now + i * 0.12 + 0.4);
                });
            } catch {
                // No audio: the badge still shows
            }
        }

        function desktop(payload) {
            if (!state.desktop || !("Notification" in window) || Notification.permission !== "granted") {
                return;
            }
            if (!document.hidden) {
                return;
            }
            const note = new Notification(payload.name || _t("New message"), { body: payload.preview || "" });
            note.onclick = () => {
                window.focus();
                action.doAction({
                    type: "ir.actions.client",
                    tag: "isd_omnichat.inbox",
                    params: { conversation_id: payload.conversation_id },
                });
                note.close();
            };
        }

        async function refreshUnread() {
            try {
                state.unread = await orm.call("omnichat.conversation", "get_unread_total", []);
            } catch {
                // keep the last count
            }
        }

        let timer = null;
        function scheduleRefresh() {
            clearTimeout(timer);
            timer = setTimeout(refreshUnread, 400);
        }

        async function setPreferences(prefs) {
            Object.assign(state, prefs);
            await orm.call("res.users", "omnichat_set_preferences", [], prefs);
            if (prefs.desktop && "Notification" in window && Notification.permission === "default") {
                Notification.requestPermission();
            }
        }

        state.isAgent = await user.hasGroup("isd_omnichat.group_omnichat_agent");
        if (state.isAgent) {
            const [prefs] = await orm.read("res.users", [user.userId], ["omnichat_sound", "omnichat_desktop"]);
            state.sound = prefs.omnichat_sound;
            state.desktop = prefs.omnichat_desktop;
            refreshUnread();
            bus_service.subscribe("isd_omnichat/update", (payload) => {
                scheduleRefresh();
                if (payload.inbound) {
                    if (state.sound) {
                        chime();
                    }
                    desktop(payload);
                }
            });
        }
        return { state, chime, refreshUnread, setPreferences };
    },
};

registry.category("services").add("isd_omnichat", omnichatService);
