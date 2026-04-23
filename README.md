<p align="center">
  <img src="assets/banner.png" alt="Ambientika Local — Home Assistant integration with local control, no cloud required" />
</p>

# Ambientika Local — Native Home Assistant Integration

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

Opens a TCP server on port 11000 (and optionally 4521) inside Home Assistant. When a fan tries to reach the Ambientika cloud, it instead hits your HA instance. Status updates are parsed and surfaced as HA entities; commands from HA are encoded as binary messages and sent back to the fan over the same TCP connection.

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
4. Confirm the port (default `11000`).

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
3. Check HA logs — you should see `Device connected from <fan IP>` followed by `Device <mac> identified: ...`.

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

Different firmware versions send slightly different status frames. This integration currently handles 19-byte (older firmware) and 21-byte (newer firmware) status messages. If your fan sends a different length, open an issue with the hex dump shown in the debug logs.

### Fan doesn't reconnect after DNS change

The fan keeps its TCP connection to the cloud open indefinitely. A simple WiFi reset on the fan rarely works — you typically need to cut power to the whole unit (pull the fuse) for ~20 seconds.

## Status & Limitations

- Tested on: Ambientika **Smart** single master device, micro firmware `0.0.11`
- Not yet implemented:
  - **Master/Slave UDP broadcasts** (zone coordination) — the protocol is known (see `ambientika-protocol.md` in the sragas repo), just not wired up here
  - **Weather update responses** — the fan periodically sends a `0x04` request asking for outdoor weather data (for Smart mode). The integration currently ignores these. A follow-up could wire this to an HA `weather` entity.
  - **BLE provisioning** — device setup must be done with the official app; after initial setup we take over via DNS redirect
- Cloud parallel operation is **not** supported. When the DNS redirect is active, the official Ambientika app stops working.

## Protocol

The binary TCP protocol used by the fans is documented in [sragas/ambientika-local-control/ambientika-protocol.md](https://github.com/sragas/ambientika-local-control/blob/main/ambientika-protocol.md). Highlights:

- Fan opens an outgoing TCP connection to `app.ambientika.eu:11000`
- Sends 18-byte firmware-info message on connect
- Sends 19 or 21-byte status messages every ~30 seconds
- Accepts 13-byte operating mode commands and 9-byte filter reset commands
- No authentication, no encryption — raw bytes on a socket

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [@sragas](https://github.com/sragas) — original protocol reverse engineering
- [@alexlenk](https://github.com/alexlenk) — polished HA add-on fork

## Disclaimer

This is an unofficial, community-developed integration. It is **not affiliated with, endorsed by, or sponsored by Südwind s.r.l.** "Südwind" and "Ambientika" are trademarks of their respective owners and are used here only to describe compatibility.
