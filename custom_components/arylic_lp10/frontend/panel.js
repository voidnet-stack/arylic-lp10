/** LP10 Control sidebar: a native Home Assistant panel, no separate server. */

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);

const css = `
  :host { display:block; min-height:100%; color:#f5f7fa; background:radial-gradient(circle at 72% -8%,rgb(60 117 145 / 16%),transparent 34rem),#080a0f; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color-scheme:dark; }
  * { box-sizing:border-box; }
  button, select, input { font:inherit; }
  button { cursor:pointer; }
  button:disabled, select:disabled, input:disabled { opacity:.45; cursor:not-allowed; }
  .shell { display:grid; grid-template-rows:76px minmax(calc(100vh - 76px),auto); grid-template-columns:268px minmax(0,1fr); min-height:100vh; border-inline:1px solid rgb(255 255 255 / 4%); }
  .topbar { position:sticky; z-index:2; top:0; grid-column:1/-1; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:0 28px; border-bottom:1px solid #252c37; background:rgb(8 10 15 / 92%); backdrop-filter:blur(18px); }
  .brand { display:inline-flex; align-items:center; gap:12px; color:#f5f7fa; font-size:1rem; font-weight:750; letter-spacing:.04em; text-decoration:none; }
  .brand b { color:#f2b84b; font-size:.72rem; letter-spacing:.16em; }
  .brand-mark { display:flex; align-items:center; justify-content:center; gap:2px; width:37px; height:37px; border:1px solid #3a424e; border-radius:50%; background:#11161d; box-shadow:inset 0 0 0 4px #0a0d12; }
  .brand-mark i { width:2px; border-radius:3px; background:#f2b84b; }
  .brand-mark i:nth-child(1),.brand-mark i:nth-child(5) { height:7px; opacity:.55; }
  .brand-mark i:nth-child(2),.brand-mark i:nth-child(4) { height:13px; opacity:.8; }
  .brand-mark i:nth-child(3) { height:19px; }
  .rail { min-width:0; padding:26px 16px; border-right:1px solid #252c37; background:#0d1016; }
  .eyebrow { color:#8f9aaa; font-size:.68rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
  .rail h2 { margin:6px 8px 16px; font-size:1.3rem; }
  .players { display:grid; gap:7px; }
  .player { width:100%; min-height:58px; padding:12px; border:1px solid transparent; border-radius:12px; background:transparent; color:inherit; text-align:left; touch-action:manipulation; }
  .player:hover, .player.selected { background:#1b202a; border-color:#343d4b; }
  .player.selected { border-left:3px solid #f2b84b; }
  .player small { display:block; margin-top:3px; color:#8f9aaa; }
  .dot { display:inline-block; width:7px; height:7px; margin-right:7px; border-radius:50%; background:#75e3a5; }
  .dot.off { background:#ff6b6b; }
  main { width:min(1412px,100%); padding:30px clamp(16px,3vw,42px) 60px; margin:auto; }
  h1 { margin:5px 0 6px; font-size:clamp(1.8rem,3vw,2.5rem); }
  h2 { margin:0; font-size:1.1rem; }
  .muted { color:#8f9aaa; }
  .subhead { margin:0 0 24px; }
  .grid { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(240px,1fr); gap:16px; }
  .card { padding:22px; border:1px solid #252c37; border-radius:18px; background:#121720; box-shadow:0 24px 70px rgb(0 0 0 / 20%); }
  .wide { grid-column:1/-1; }
  .now { display:grid; grid-template-columns:140px minmax(0,1fr); gap:20px; align-items:center; }
  .art { width:140px; height:140px; display:grid; place-items:center; border-radius:14px; background:linear-gradient(130deg,#283645,#0d131b); color:#718294; font-size:3rem; overflow:hidden; }
  .art img { width:100%; height:100%; object-fit:cover; }
  .track { margin:8px 0 2px; font-size:1.45rem; overflow-wrap:anywhere; }
  .controls { display:flex; align-items:center; gap:8px; margin-top:20px; }
  .controls button { width:44px; height:44px; border:1px solid #343d4b; border-radius:50%; background:#171d27; color:inherit; }
  .controls .primary { width:54px; height:54px; background:#f2b84b; color:#171106; border-color:#f2b84b; font-weight:800; }
  .row { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .row + .row { margin-top:18px; }
  .row label { font-weight:700; }
  .volume { display:flex; align-items:center; gap:14px; margin:28px 0 19px; }
  .volume input { width:100%; accent-color:#f2b84b; }
  .volume strong { min-width:48px; text-align:right; }
  .pill { display:inline-flex; min-height:42px; align-items:center; justify-content:center; gap:8px; padding:8px 14px; border:1px solid #343d4b; border-radius:10px; background:#151a22; color:inherit; text-decoration:none; font-weight:650; }
  .pill:hover { border-color:#515d6d; background:#1a202a; }
  .pill.active { border-color:#f2b84b; color:#f2b84b; }
  select { width:100%; min-height:44px; margin-top:14px; padding:0 10px; border:1px solid #343d4b; border-radius:9px; background:#151a22; color:inherit; }
  .details { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; margin-top:17px; border:1px solid #303841; border-radius:10px; overflow:hidden; background:#303841; }
  .detail { padding:13px 15px; background:#121720; min-width:0; }
  .detail small { display:block; color:var(--secondary-text-color,#98a3af); }
  .detail strong { display:block; margin-top:5px; font-size:.84rem; overflow-wrap:anywhere; }
  .warning { margin-top:16px; padding:12px; border:1px solid #725254; border-radius:9px; background:#332125; color:#f1b8bb; }
  .repair-status { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-top:16px; padding:16px; border:1px solid var(--divider-color,#303841); border-radius:12px; background:var(--card-background-color,#1a212a); }
  .sound-controls { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px 20px; margin-top:18px; }
  .sound-control { min-width:0; }
  .sound-control label, .sound-control .label-row { display:flex; justify-content:space-between; gap:12px; margin-bottom:7px; font-size:.88rem; }
  .sound-control input[type=range] { width:100%; accent-color:#f2b84b; }
  .eq-bands { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 20px; margin-top:14px; }
  .custom-eq-editor { margin-top:16px; padding-top:14px; border-top:1px solid var(--divider-color,#303841); }
  .custom-eq-editor input[type=text] { width:100%; margin-top:6px; padding:10px; border:1px solid #343d4b; border-radius:9px; background:#151a22; color:inherit; }
  .quality { display:flex; gap:8px; margin-top:10px; }
  .quality span { padding:5px 8px; border:1px solid var(--divider-color,#303841); border-radius:8px; color:var(--secondary-text-color,#98a3af); font-size:.78rem; }
  .timeline { margin-top:16px; }
  .timeline progress { width:100%; height:7px; accent-color:#f2b84b; }
  .timeline-times { display:flex; justify-content:space-between; color:var(--secondary-text-color,#98a3af); font-size:.75rem; }
  .section-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
  .advanced-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
  .hint { margin:12px 0 0; color:var(--secondary-text-color,#98a3af); font-size:.82rem; }
  .source-row { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
  .source-row button { border:1px solid var(--divider-color,#303841); border-radius:9px; padding:8px 11px; background:transparent; color:inherit; }
  .source-row button.active { border-color:#f2b84b; color:#f2b84b; }
  a:focus-visible, button:focus-visible { outline:3px solid rgb(100 215 255 / 52%); outline-offset:3px; }
  .empty { padding:34px; text-align:center; }
  @media(max-width:780px) { .shell { display:block; } .topbar { position:sticky; height:64px; padding:0 14px; } .rail { border-right:0; border-bottom:1px solid #252c37; padding:15px; } .rail h2 { display:none; } .players { display:flex; overflow-x:auto; } .player { min-width:170px; } main { padding-top:24px; } .grid { grid-template-columns:1fr; } .wide { grid-column:1; } .details { grid-template-columns:repeat(2,minmax(0,1fr)); } }
  @media(max-width:600px) { .sound-controls, .eq-bands, .advanced-grid { grid-template-columns:1fr; } }
  @media(max-width:480px) { .now { grid-template-columns:86px minmax(0,1fr); gap:14px; } .art { width:86px; height:86px; } .track { font-size:1.1rem; } .controls { grid-column:1/-1; } }
`;

class ArylicLP10Panel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._selected = null;
    try { this._selected = localStorage.getItem("arylic-lp10-selected"); } catch (_) { /* Storage can be disabled by browser policy. */ }
    this._dragging = false;
    this._queued = false;
    this._editingCustomEq = false;
    this._eqSaving = false;
    this.shadowRoot.addEventListener("pointerdown", (event) => {
      const playerButton = event.target.closest?.('[data-action="player"]');
      if (playerButton && !playerButton.disabled) this._selectPlayer(playerButton.dataset.id);
    });
    this.shadowRoot.addEventListener("click", (event) => this._click(event));
    this.shadowRoot.addEventListener("input", (event) => {
      if (event.target.matches("[data-volume], [data-number-entity]")) {
        this._dragging = true;
        const output = event.target.matches("[data-volume]")
          ? this.shadowRoot.querySelector("[data-volume-output]")
          : this.shadowRoot.querySelector(`[data-number-output="${CSS.escape(event.target.dataset.numberEntity)}"]`);
        const suffix = event.target.matches("[data-volume]") ? "%" : (event.target.id.includes("balance") ? "%" : " dB");
        if (output) output.textContent = `${event.target.value}${suffix}`;
      } else if (event.target.matches("[data-custom-gain]")) {
        this._editingCustomEq = true;
        const output = event.target.parentElement.querySelector("output");
        if (output) output.textContent = `${event.target.value} dB`;
      } else if (event.target.matches("[data-custom-eq-name]")) {
        this._editingCustomEq = true;
      }
    });
    this.shadowRoot.addEventListener("change", (event) => this._change(event));
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._dragging && !this._editingCustomEq && !this._eqSaving && !this._queued) {
      this._queued = true;
      queueMicrotask(() => { this._queued = false; this._render(); });
    }
  }

  connectedCallback() { this._render(); }

  _entities() {
    return Object.values(this._hass?.states || {}).filter(
      (state) => state.attributes?.lp10_identity && state.attributes?.lp10_role,
    );
  }

  _render() {
    const entities = this._entities();
    const players = entities.filter((state) => state.attributes.lp10_role === "media_player")
      .sort((a, b) => (a.attributes.friendly_name || "").localeCompare(b.attributes.friendly_name || ""));
    if (!players.some((state) => state.attributes.lp10_identity === this._selected)) {
      this._selected = players[0]?.attributes.lp10_identity || null;
    }
    const player = players.find((state) => state.attributes.lp10_identity === this._selected);
    const related = entities.filter((state) => state.attributes.lp10_identity === this._selected);
    const role = (name) => related.find((state) => state.attributes.lp10_role === name);
    const stateValue = (name) => {
      const value = role(name)?.state;
      return value && !["unknown", "unavailable"].includes(value) ? value : "—";
    };
    const button = (label, action, disabled, className = "") =>
      `<button class="${className}" data-action="${action}" ${disabled ? "disabled" : ""} aria-label="${label}">${label}</button>`;
    const playerList = players.map((item) => {
      const identity = item.attributes.lp10_identity;
      const active = identity === this._selected;
      return `<button class="player ${active ? "selected" : ""}" data-action="player" data-id="${escapeHtml(identity)}" aria-pressed="${active}">
        <span class="dot ${item.state === "unavailable" ? "off" : ""}"></span>${escapeHtml(item.attributes.friendly_name || "LP10")}
        <small>${escapeHtml(item.state)}</small></button>`;
    }).join("");
    const unavailable = !player || player.state === "unavailable";
    const attrs = player?.attributes || {};
    const metadataDisabled = attrs.metadata_status === "disabled";
    const metadataNotice = !metadataDisabled && ["unknown", "unreachable", "no_track_data"].includes(attrs.metadata_status)
      ? `<p class="hint">${attrs.metadata_status === "unreachable" ? "Track details are temporarily unavailable. Playback and basic controls remain available." : attrs.metadata_status === "no_track_data" ? "The player is responding but has no track details for this source." : "Playback details are not available yet."}</p>`
      : "";
    const image = attrs.entity_picture || attrs.media_image_url;
    const cover = image ? `<img src="${escapeHtml(image)}" alt="Album artwork">` : "♫";
    const volume = Math.round((attrs.volume_level || 0) * 100);
    const display = role("front_display");
    const restart = role("restart");
    const repairStatus = role("repair_status");
    const repairButton = role("repair");
    const eqPreset = role("eq_preset");
    const deepBass = role("deep_bass");
    const soundNumber = (entityRole, label, minimum, maximum, unit = "") => {
      const entity = role(entityRole);
      if (!entity) return "";
      const value = Number(entity.state);
      const shown = Number.isFinite(value) ? value : minimum;
      return `<div class="sound-control"><label for="${escapeHtml(entity.entity_id)}"><span>${escapeHtml(label)}</span><strong data-number-output="${escapeHtml(entity.entity_id)}">${escapeHtml(shown)}${escapeHtml(unit)}</strong></label>
        <input id="${escapeHtml(entity.entity_id)}" type="range" min="${minimum}" max="${maximum}" step="1" value="${shown}" data-number-entity="${escapeHtml(entity.entity_id)}" ${entity.state === "unavailable" ? "disabled" : ""}></div>`;
    };
    const presetOptions = (eqPreset?.attributes?.options || []).map((option) =>
      `<option value="${escapeHtml(option)}" ${eqPreset.state === option ? "selected" : ""}>${escapeHtml(option)}</option>`
    ).join("");
    const customEq = eqPreset?.state === "Custom";
    const eqBands = Array.from({ length: 8 }, (_, index) => {
      const band = role(`eq_band_${index + 1}`);
      if (!band) return "";
      const frequency = band.attributes.frequency_hz;
      return soundNumber(`eq_band_${index + 1}`, frequency ? `${frequency} Hz` : `Band ${index + 1}`, -10, 10, " dB");
    }).join("");
    const customGainControls = Array.from({ length: 8 }, (_, index) => {
      const band = role(`eq_band_${index + 1}`);
      const frequency = band?.attributes.frequency_hz || [125, 250, 500, 1000, 2000, 4000, 8000, 16000][index];
      const currentGain = customEq && Number.isFinite(Number(band?.state)) ? Number(band.state) : 0;
      const frequencyLabel = frequency >= 1000 ? `${frequency / 1000}k` : String(frequency);
      return `<label class="sound-control"><span class="label-row"><span>${frequencyLabel} Hz</span><output>${currentGain} dB</output></span>
        <input type="range" min="-10" max="10" step="1" value="${currentGain}" data-custom-gain></label>`;
    }).join("");
    const customProfiles = (eqPreset?.attributes?.custom_eq_profiles || []).join(", ");
    const diagnosticFields = [
      ["Device name", "name"], ["IP address", "ip_address"],
      ["Software version", "software_version"], ["Firmware version", "firmware_version"],
      ["MCU version", "mcu_version"], ["MAC address", "mac_address"], ["Connectivity", "connectivity"],
      ["Serial number", "serial_number"], ["Uptime", "uptime_seconds"],
      ["Sample rate", "sample_rate"], ["Bit depth", "bit_depth"],
    ];
    const details = diagnosticFields.map(([label, key]) =>
      `<div class="detail"><small>${escapeHtml(label)}</small><strong>${escapeHtml(stateValue(key))}</strong></div>`
    ).join("");
    const repairLabels = {
      checking: "Checking over SSH",
      backing_up: "Saving verified backups",
      staging: "Copying and verifying the fix",
      switching: "Switching the status service",
      verifying: "Checking TCP/5555",
      rolling_back: "Restoring the original service",
      complete: "Fix applied successfully",
      repair_failed: "Repair stopped",
      ssh_unavailable: "SSH unavailable",
      fix_available: "Supported stock adbd; fix available",
      patched: "AOSP fix is running; TCP/5555 healthy",
      patched_service_unavailable: "Patched binary running; TCP/5555 did not respond",
      unsupported_firmware: "Unsupported adbd binary; no change made",
      unknown_candidate: "Unknown candidate process; no change made",
    };
    const repairCard = repairStatus ? `<section class="repair-status wide">
      <div><div class="eyebrow">Optional maintenance</div>
        <strong>${escapeHtml(repairLabels[repairStatus.state] || repairStatus.state)}</strong>
        ${repairStatus.attributes?.progress_message ? `<div class="muted" style="margin-top:5px">${escapeHtml(repairStatus.attributes.progress_message)}</div>` : ""}
      </div>
      ${button(repairStatus.attributes?.in_progress ? "Repairing…" : "Apply ADB fix", "repair", repairStatus.state !== "fix_available" || !repairButton || repairStatus.attributes?.in_progress)}
    </section>` : "";
    const formatTime = (seconds) => {
      if (!Number.isFinite(seconds) || seconds < 0) return "–:––";
      const whole = Math.floor(seconds);
      return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
    };
    const duration = Number(attrs.media_duration);
    const position = Number(attrs.media_position);
    const positionUpdated = Date.parse(attrs.media_position_updated_at || "");
    const livePosition = Number.isFinite(position)
      ? Math.min(Number.isFinite(duration) ? duration : Infinity,
        position + (player.state === "playing" && Number.isFinite(positionUpdated) ? Math.max(0, (Date.now() - positionUpdated) / 1000) : 0))
      : null;
    const timeline = Number.isFinite(duration) && duration > 0 && livePosition !== null
      ? `<div class="timeline"><progress max="${duration}" value="${livePosition}"></progress><div class="timeline-times"><span>${formatTime(livePosition)}</span><span>${formatTime(duration)}</span></div></div>` : "";
    const quality = [attrs.sample_rate ? `${(Number(attrs.sample_rate) / 1000).toFixed(Number(attrs.sample_rate) % 1000 ? 1 : 0)} kHz` : "", attrs.bit_depth ? `${attrs.bit_depth} bit` : ""]
      .filter(Boolean).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
    const sourceButtons = (attrs.source_list || []).map((source) =>
      `<button class="${attrs.source === source ? "active" : ""}" data-source-value="${escapeHtml(source)}" ${unavailable ? "disabled" : ""}>${escapeHtml(source)}</button>`
    ).join("");
    this.shadowRoot.innerHTML = `<style>${css}</style><div class="shell">
      <header class="topbar"><a class="brand" href="/arylic_lp10" aria-label="LP10 Control home">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span><span>LP10 <b>CONTROL</b></span>
      </a></header>
      <aside class="rail"><h2>Players</h2><div class="players">${playerList}</div></aside>
      <main>${player ? `<div class="row"><div><div class="eyebrow">Now controlling</div><h1>${escapeHtml(attrs.friendly_name || "LP10")}</h1></div>${button("Refresh", "refresh", unavailable, "pill")}</div>
        <p class="muted subhead">${escapeHtml(player.state)} · ${escapeHtml(stateValue("ip_address"))}</p>
        <div class="grid">
          <section class="card now"><div class="art">${cover}</div><div><div class="eyebrow">${metadataDisabled ? "Playback controls" : "Now playing"}</div>
            <div class="track">${escapeHtml(metadataDisabled ? "Track details disabled" : (attrs.media_title || "Nothing playing"))}</div>
            <div class="muted">${metadataDisabled ? "Controls-only mode" : escapeHtml([attrs.media_artist, attrs.media_album_name].filter(Boolean).join(" · "))}</div>
            ${quality ? `<div class="quality">${quality}</div>` : ""}${timeline}
            ${metadataNotice}
            <div class="controls">
              ${button("⏮", "previous", unavailable)}
              ${button(metadataDisabled ? "⏯" : (player.state === "playing" ? "Ⅱ" : "▶"), "play_pause", unavailable, "primary")}
              ${button("⏭", "next", unavailable)}
            </div></div></section>
          <section class="card"><div class="eyebrow">Volume</div><h2>Listening level</h2>
            <div class="volume"><input type="range" min="0" max="100" value="${volume}" data-volume ${unavailable ? "disabled" : ""} aria-label="Volume">
            <strong data-volume-output>${volume}%</strong></div>
            ${button(attrs.is_volume_muted ? "Unmute" : "Mute", "mute", unavailable, attrs.is_volume_muted ? "pill active" : "pill")}
            ${soundNumber("max_volume", "Maximum volume", 30, 100, "%")}
          </section>
          <section class="card"><div class="eyebrow">Input</div><h2>Choose a source</h2><div class="source-row">${sourceButtons}</div>
          </section>
          <section class="card wide"><div class="section-heading"><div><div class="eyebrow">Sound shaping</div><h2>Tone and balance</h2></div></div>
            <div class="sound-controls">${soundNumber("treble", "Treble", -10, 10, " dB")}${soundNumber("mid", "Mid", -10, 10, " dB")}${soundNumber("bass", "Bass", -10, 10, " dB")}${soundNumber("balance", "Balance", -100, 100, "%")}</div>
            <button class="pill" data-action="reset-tones" ${unavailable ? "disabled" : ""} style="margin-top:14px">Reset tone</button>
            <div class="row" style="margin-top:18px"><strong>Deep Bass</strong>${button(deepBass?.state === "on" ? "On" : "Off", "deep_bass", !deepBass || deepBass.state === "unavailable", deepBass?.state === "on" ? "pill active" : "pill")}</div>
            <div class="sound-controls">${soundNumber("deep_bass_intensity", "Deep Bass intensity", 0, 100, "%")}</div>
          </section>
          <section class="card wide"><div class="eyebrow">Equaliser</div><h2>EQ preset</h2>
            ${eqPreset ? `<select data-select-entity="${escapeHtml(eqPreset.entity_id)}" ${eqPreset.state === "unavailable" ? "disabled" : ""} aria-label="EQ preset">${presetOptions}</select>` : '<p class="muted">EQ preset control is unavailable.</p>'}
            ${customEq ? `<div class="eq-bands">${eqBands}</div>` : ""}
            <div class="custom-eq-editor"><label>Custom profile name<input type="text" maxlength="32" value="LP10 Custom" data-custom-eq-name></label>
              <div class="eq-bands">${customGainControls}</div>
              <button class="pill" data-action="apply-custom-eq" ${this._eqSaving ? "disabled" : ""} style="margin-top:14px">${this._eqSaving ? "Saving custom EQ…" : "Create and apply custom EQ"}</button>
              ${customProfiles ? `<p class="hint">Saved custom slot: ${escapeHtml(customProfiles)}</p>` : '<p class="hint">Creates or replaces the LP10’s named custom EQ slot.</p>'}</div>
          </section>
          <section class="card"><div class="eyebrow">Player hardware</div><h2>Front display</h2>
            <div class="row" style="margin-top:20px"><span class="muted">Display light</span>
            ${button(display?.state === "on" ? "Turn off" : "Turn on", "display", unavailable || !display, "pill")}</div>
            <div class="row"><span class="muted">Restart the player</span>
            ${button("Restart", "restart", !restart || restart.state === "unavailable", "pill")}</div>
          </section>
          <section class="card wide"><div class="eyebrow">About this player</div><h2>Device information</h2>
            <div class="details">${details}</div></section>
        </div>${repairCard}${unavailable ? '<div class="warning">This LP10 is not currently responding.</div>' : ""}` :
        '<div class="card empty">No Arylic LP10 entities are configured yet. Add one in Settings → Devices & services.</div>'}
      </main></div>`;
  }

  async _call(entity, domain, service, extra = {}) {
    if (!entity || !this._hass) return;
    try {
      await this._hass.callService(domain, service, { entity_id: entity.entity_id, ...extra });
    } catch (error) {
      alert(`LP10 control failed: ${error?.message || error}`);
    }
  }

  _related(role) {
    return this._entities().find((state) =>
      state.attributes.lp10_identity === this._selected && state.attributes.lp10_role === role);
  }

  _selectPlayer(identity) {
    if (!identity || identity === this._selected) return;
    this._selected = identity;
    this._editingCustomEq = false;
    try { localStorage.setItem("arylic-lp10-selected", identity); } catch (_) { /* Storage can be disabled by browser policy. */ }
    this._render();
  }

  async _click(event) {
    const target = event.target.closest("[data-action], [data-source-value]");
    if (!target || target.disabled) return;
    const action = target.dataset.action;
    if (action === "player") {
      this._selectPlayer(target.dataset.id);
      return;
    }
    if (action === "refresh") {
      await this._call(this._related("media_player"), "homeassistant", "update_entity");
      return;
    }
    if (action === "apply-custom-eq") {
      const entity = this._related("eq_preset");
      const name = this.shadowRoot.querySelector("[data-custom-eq-name]")?.value.trim();
      const bands = [...this.shadowRoot.querySelectorAll("[data-custom-gain]")].map((input) => Number(input.value));
      if (!entity || !name) {
        alert("Enter a name for the custom EQ profile.");
        return;
      }
      this._eqSaving = true;
      this._editingCustomEq = false;
      this._render();
      try {
        await this._hass.callService("arylic_lp10", "create_custom_eq", {
          entity_id: entity.entity_id, name, bands,
        });
      } catch (error) {
        alert(`Could not apply the custom EQ: ${error?.message || error}`);
      } finally {
        this._eqSaving = false;
        this._render();
      }
      return;
    }
    if (action === "reset-tones") {
      for (const entityRole of ["treble", "mid", "bass", "balance"]) {
        const entity = this._related(entityRole);
        if (entity) await this._call(entity, "number", "set_value", { value: 0 });
      }
      return;
    }
    if (target.dataset.sourceValue) {
      await this._call(this._related("media_player"), "media_player", "select_source", {
        source: target.dataset.sourceValue,
      });
      return;
    }
    const media = this._related("media_player");
    if (action === "play_pause") await this._call(this._related("playback_toggle"), "button", "press");
    if (action === "previous") await this._call(media, "media_player", "media_previous_track");
    if (action === "next") await this._call(media, "media_player", "media_next_track");
    if (action === "mute") await this._call(media, "media_player", "volume_mute", { is_volume_muted: !media?.attributes.is_volume_muted });
    if (action === "deep_bass") {
      const entity = this._related("deep_bass");
      await this._call(entity, "switch", entity?.state === "on" ? "turn_off" : "turn_on");
    }
    if (action === "display") {
      const entity = this._related("front_display");
      await this._call(entity, "switch", entity?.state === "on" ? "turn_off" : "turn_on");
    }
    if (action === "restart" && confirm("Restart this LP10? Playback will stop briefly.")) {
      await this._call(this._related("restart"), "button", "press");
    }
    if (action === "repair" && confirm(
      "Apply the verified ADB fix to this LP10? The status service may briefly reconnect. Audio playback is not intentionally stopped. The fix is temporary and will be lost at player reboot."
    )) {
      await this._call(this._related("repair"), "button", "press");
    }
  }

  async _change(event) {
    if (event.target.matches("[data-volume]")) {
      this._dragging = false;
      await this._call(this._related("media_player"), "media_player", "volume_set", {
        volume_level: Number(event.target.value) / 100,
      });
    }
    if (event.target.matches("[data-number-entity]")) {
      this._dragging = false;
      await this._call({ entity_id: event.target.dataset.numberEntity }, "number", "set_value", {
        value: Number(event.target.value),
      });
    }
    if (event.target.matches("[data-select-entity]")) {
      await this._call({ entity_id: event.target.dataset.selectEntity }, "select", "select_option", {
        option: event.target.value,
      });
    }
  }
}

customElements.define("arylic-lp10-panel", ArylicLP10Panel);
