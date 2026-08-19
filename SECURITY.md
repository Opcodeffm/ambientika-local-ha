# Security policy

Ambientika Local receives unauthenticated binary traffic from LAN devices. Security reports and diagnostic evidence therefore need more care than ordinary bug reports.

## Reporting a vulnerability

Do not publish a working exploit, real device identifier, account credential, token, packet capture, or customer data in a GitHub issue.

Use GitHub's private vulnerability reporting feature when it is available for this repository. If it is not available, open a minimal issue asking the maintainer for a private security contact without including technical details or sensitive evidence.

Please include privately:

- affected integration version and Home Assistant version;
- device model and firmware version;
- required network position and other prerequisites;
- expected and observed behaviour;
- minimal reproduction steps using devices and accounts you own or are authorised to test;
- a redacted request, response, or frame description;
- suggested remediation, if known.

## Data handling

Before sharing evidence, remove or irreversibly redact:

- full MAC addresses and device identifiers;
- LAN and public IP addresses;
- account names and email addresses;
- JWTs, cookies, passwords, API keys, MQTT credentials, and session identifiers;
- precise household locations and unaggregated telemetry;
- unrelated data belonging to another person.

Do not rely on a visual overlay inside an editable document. Export redacted screenshots to a new raster image, remove metadata, and verify the result before sharing it.

## Supported versions

Security fixes are applied to the latest published beta. Older beta releases may not receive backports.

## Trust model

The vendor protocol contains no cryptographic device authentication, encryption, or message integrity. This integration cannot add a secret to existing device firmware. Its explicit enrollment window, quarantined candidates, device allowlist, source-IP bindings, firmware-bound write approval, connection rules, parser validation, and resource limits are compensating controls.

For a production installation:

1. configure the expected device identifiers or use only the five-minute enrollment window;
2. explicitly approve expected quarantined candidates and leave unknown candidates unapproved;
3. enable command writes only after verifying the locally observed firmware profile;
4. optionally bind each identifier to a static DHCP address;
5. allow only the fan network to reach Home Assistant TCP 11000;
6. block fan WAN access except for deliberately provided local infrastructure;
7. keep legacy TCP 4521 disabled unless it is proven necessary;
8. never expose either listener through an internet port forward.

An empty allowlist no longer creates an open discovery listener. Enrollment must be requested explicitly, expires after five minutes, and only records bounded candidate metadata before closing the socket. This is still not cryptographic authentication; verify candidates against owned hardware and network records before approval.
