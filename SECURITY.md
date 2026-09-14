# Security

Report security issues through GitHub private security advisories rather than a
public issue.

The relay API must be served over HTTPS. Configure a high-entropy bearer token in
the server environment, Linux user configuration, and Android private app storage.
Do not put production endpoints, tokens, device identifiers, personal calibration
data, or deployment credentials in source control.

The API intentionally stores cadence only in process memory and expires it after a
short timeout. Deploy it with one application worker unless the state layer is
replaced with a shared store.
