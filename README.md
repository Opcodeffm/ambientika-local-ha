<p align="center">
  <img src="assets/banner.png" alt="Ambientika Local — Home Assistant integration with local control, no cloud required" />
</p>

# Ambientika Local — Native Home Assistant Integration

![Status: Beta](https://img.shields.io/badge/status-beta-orange) ![Version](https://img.shields.io/badge/version-0.3.0--beta-blue) ![HA: 2024.6+](https://img.shields.io/badge/Home%20Assistant-2024.6%2B-blue)

> ⚠️ **Beta software.** Tested with one device (Ambientika Smart, firmware micro 0.0.11 / radio 0.0.21). Other firmware versions may behave differently. If you run into issues, please [open one](https://github.com/Opcodeffm/ambientika-local-ha/issues) — include the firmware version and frame length, but never post an unredacted device identifier or capture.

A **native Home Assistant custom component** for Südwind Ambientika smart ventilation devices. Communicates directly with the fans over TCP — **no cloud, no MQTT, no Docker add-on, no middleware**. Just drop it into `custom_components/` and go.

## Why another Ambientika integration?

There are already two projects for local Ambientika control:

- [**sragas/ambientika-local-control**](https://github.com/sragas/ambientika-local-control) — Node.js tool, MQTT-based. Did the original protocol reverse-engineering work that made all of this possible.
- [**alexlenk/ambientika-local-control-ha-addon**](https://github.com/alexlenk/ambientika-local-control-ha-addon) — Polished HA Add-on, still MQTT-based, great for multi-zone setups with master/slave pairs.

Both are excellent, but both rely on **MQTT**. If you don't run a broker (or simply don't like MQTT), you're out of luck.

**This integration** is a native HA custom component:

| | sragas / alexlenk | This integration |
|---|---|---|
| Installation | Docker / Add-on | Custom Component |
| HA bridge | **MQTT** | **Native entities** |
| Dependencies | Mosquitto + add-on | None |
| Multi-zone | yes | not yet |
| BLE provisioning | yes | no (DNS redirect) |
| Cloud sync | optional | no |

Credit where it's due — the binary TCP protocol specification used here is based on the reverse-engineering work by [@sragas](https://github.com/sragas). This project exists only because that documentation does. ❤️

## What it does

Opens a bounded TCP server on port 11000 inside Home Assistant. Legacy port 4521 is disabled by default and should only be enabled if a packet capture proves that a particular firmware needs it. When a fan tries to reach the Ambientika cloud, it instead hits your HA instance. Status updates are parsed and surfaced as HA entities; commands from HA are encoded as binary messages and sent back to the fan over the same TCP connection.

## Entities

Per connected device:

- **Climate** — HVAC mode (off / fan_only), fan speed (low/medium/high), preset modes (Smart, Auto, Heat Recovery, Night, Away, Surveillance, Timed Expulsion, Expulsion, Intake)
- **Sensors** — Temperature (°C), Humidity (%), Air Quality, WiFi Signal
- **Binary Sensors** — Filter status (problem), Humidity Alarm, Night Alarm
- **Button** — Filter Reset

## Installation

### Manual

1. Copy `custom_components/ambientika_local/` into your HA `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for "Ambientika Local".
4. Either enter the identifiers of devices you own or explicitly open the five-minute enrollment window. With neither, setup stays fail-closed and opens no TCP listener.

### HACS (custom repository)

1. In HACS: **Integrations → ⋮ menu → Custom repositories**
2. Add `https://github.com/Opcodeffm/ambientika-local-ha` as type "Integration"
3. Install "Ambientika Local"
4. Restart HA and add the integration via the UI

## DNS Redirect (required)

Your fan is hard-coded to connect to `app.ambientika.eu:11000`. For the local integration to work, you need to redirect that hostname to your Home Assistant IP on your local network.

Examples:

### UniFi (UDM / UCG)
**Settings → Policy Engine → DNS** → create policy:
- Type: `Host (A)`
- Domain Name: `app.ambientika.eu`
- IP Address: `<your HA IP>`

### Fritz!Box
Unfortunately, the Fritz!Box can't override external DNS names out of the box. Use Pi-hole, AdGuard Home, or another DNS server on your network.

### Pi-hole / AdGuard Home
Add a local DNS record:
- `app.ambientika.eu` → `<your HA IP>`

After setting this up:
1. Verify from your computer: `nslookup app.ambientika.eu` should return your HA IP.
2. **Power-cycle the fan** (pull the fuse briefly — the WiFi module holds the TCP connection open until it loses power).
3. Check HA logs — peer IPs and device identifiers are deliberately shortened in log messages.

## Secure enrollment and command approval

The integration has three deliberately separate trust levels:

1. **Unknown:** no listener is opened unless at least one device is already approved or the owner explicitly starts enrollment.
2. **Quarantined candidate:** during the five-minute enrollment window, an unknown identifier and its redacted source metadata may be recorded. Its socket is immediately closed, no entity is created, and no command can be sent.
3. **Approved device:** only an explicit selection in **Settings → Devices & Services → Ambientika Local → Configure** moves a candidate to the allowlist. Enabling command writes is a second, separate selection tied to the firmware profile observed locally.

For first-time enrollment:

1. Add the integration and select **Open a five-minute enrollment window**.
2. Apply the DNS redirect, then power-cycle the owned fan so that it connects while the window is open.
3. Open the integration's **Configure** dialog. Verify the redacted identifier suffix, source subnet and firmware, then select only the expected candidate.
4. Keep **Bind newly approved devices to their observed IP** enabled if the fan has a stable DHCP reservation.
5. Select command control only if the shown firmware belongs to the expected fan. Leaving it unselected gives you read-only telemetry.

If the firmware identification was not seen during enrollment, first approve only the device and let it reconnect. Command control can be enabled in a second pass after its firmware profile appears. A later firmware change automatically blocks writes until the owner approves the new observed profile; telemetry can continue.

The security options also include:

- **Approved device IDs** — comma-separated 12-character MAC/device identifiers. Colons and dashes are accepted.
- **Device-to-IP bindings** — optional entries such as `AABBCCDDEEFF=192.0.2.10`. A binding also adds that device to the allowlist.
- **Require firmware identification** — enabled by default. Disable it only for an older owned device that demonstrably never sends the 18-byte firmware frame. Such a connection still cannot receive commands without a verified firmware handshake.
- **Listener IP address** — use the specific Home Assistant interface on the fan network where possible. If `0.0.0.0` or `::` is necessary, Home Assistant Repairs warns about the broader exposure and the firewall must enforce the boundary.
- **Status frame length** — keep `auto` unless observed traffic requires an explicit `19` or `21` byte selection.
- **Legacy port 4521** — leave disabled unless actual traffic proves that it is necessary.

The listener additionally enforces one identity per socket, rejects active duplicate identities, limits global, per-IP and unidentified connections, rate-limits reconnects, frames and commands, bounds stream memory, times out incomplete frames and idle clients, coalesces duplicate mode writes, and throttles remote-triggered logs.

These checks are defence in depth, not cryptographic device authentication. Ambientika's native protocol has no secret or signature that this integration can verify. An allowlist or source-IP binding cannot stop an attacker who can spoof an approved fan inside the same LAN. Network isolation remains mandatory.

### Existing installations

When an older config entry is migrated, the integration imports Ambientika identifiers already associated with that entry in Home Assistant's device registry. This keeps owned devices admitted without restoring open discovery. Command writes are intentionally disabled during migration until each observed firmware profile is explicitly approved. If no owned identifier can be recovered, the listener remains closed and Home Assistant Repairs explains how to open a bounded enrollment window.

### Recommended firewall policy

- Fans may reach the HA host on TCP 11000.
- Fans may reach only local DNS and, if required, local NTP.
- Fans have no direct WAN access.
- Other IoT clients and guest networks cannot reach TCP 11000.
- TCP 4521 remains closed unless explicitly required.

Do not rely on blocking one historical cloud IP. Use the local DNS override together with an outbound firewall policy because vendor addresses can change.

### Diagnostics and Repairs

Use the integration menu in **Settings → Devices & Services** to download diagnostics. The report contains policy state, aggregate counters, redacted identifier suffixes, shortened IP networks and firmware profiles. It does not contain complete device identifiers, exact peer IPs, frames, credentials or cloud data.

Home Assistant Repairs raises an issue when no approved device exists and the listener is therefore closed. It also warns when the listener is bound to every interface. These issues describe configuration risk; they are not evidence of a vendor-cloud vulnerability.

## Troubleshooting

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ambientika_local: debug
```

Then restart HA and look under **Settings → System → Logs** for `ambientika_local`.

### Status messages not parsed ("Unknown message")

Different firmware versions send slightly different status frames. This integration handles 19-byte and 21-byte status messages using a persistent TCP stream buffer. If automatic detection is ambiguous, select the observed size in the integration options. For an issue report, include only the frame type and length plus an integration-generated redacted log line.

### Fan doesn't reconnect after DNS change

The fan keeps its TCP connection to the cloud open indefinitely. A simple WiFi reset on the fan rarely works — you typically need to cut power to the whole unit (pull the fuse) for ~20 seconds.

## Status & Limitations

- Tested on: Ambientika **Smart** single master device, micro firmware `0.0.11`
- Not yet implemented:
  - **Master/Slave UDP broadcasts** (zone coordination) — the protocol is known (see `ambientika-protocol.md` in the sragas repo), just not wired up here
  - **Weather updates** — in Smart mode, the cloud may send `0x04` outdoor-weather frames to the fan. This integration can encode the frame but does not yet source or schedule weather data from an HA `weather` entity.
  - **BLE provisioning** — device setup must be done with the official app; after initial setup we take over via DNS redirect
- Cloud parallel operation is **not** supported. When the DNS redirect is active, the official Ambientika app stops working.
- There is no permanent open-discovery mode. Enrollment is explicit, expires after five minutes and records candidates without admitting them.

## Protocol

The binary TCP protocol used by the fans is documented in [sragas/ambientika-local-control/ambientika-protocol.md](https://github.com/sragas/ambientika-local-control/blob/master/ambientika-protocol.md). Highlights:

- Fan opens an outgoing TCP connection to `app.ambientika.eu:11000`
- Sends 18-byte firmware-info message on connect
- Sends 19 or 21-byte status messages every ~30 seconds
- Accepts 13-byte operating mode commands and 9-byte filter reset commands
- No authentication, no encryption — raw bytes on a socket

## Security and privacy

- Debug logs redact the embedded MAC/device identifier and shorten peer IP addresses.
- Unknown devices never create entities, and devices are read-only until an observed firmware profile is separately approved for command writes.
- Do not publish PCAP files, raw frames, JWTs, cloud credentials, account email addresses, exact device IPs, or full device identifiers.
- Keep TCP 11000 private to the fan network; never forward it from the internet.
- See [SECURITY.md](SECURITY.md) for the private-reporting and evidence-handling policy.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [@sragas](https://github.com/sragas) — original protocol reverse engineering
- [@alexlenk](https://github.com/alexlenk) — polished HA add-on fork

## Disclaimer

This is an unofficial, community-developed integration. It is **not affiliated with, endorsed by, or sponsored by Südwind s.r.l.** "Südwind" and "Ambientika" are trademarks of their respective owners and are used here only to describe compatibility.
