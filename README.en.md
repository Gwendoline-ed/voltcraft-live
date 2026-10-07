# Voltcraft Live

[Deutsche Anleitung](README.md) · GNU GPLv3 or later · Version 0.1.0

View **Voltcraft DSO-1084F** waveforms in a browser on Linux, control the instrument, and save your own measurement profiles. The application is a single Python file using only the Python standard library. Its interface is currently in German.

## Features

- Repeated USB waveform acquisitions with a time axis and CH1–CH4 visibility controls.
- Mouse-wheel time zoom anchored at the pointer; Shift + wheel for amplitude zoom; drag to pan; double-click to reset.
- Run, Stop, single acquisition, Auto Scale, and force trigger.
- Channel enable, coupling, probe attenuation, V/div, offset, bandwidth limit, inversion, timebase, and edge-trigger settings.
- Read device settings and read them back after applying changes.
- Persistent editable profiles with explicit overwrite protection and JSON import/export.
- PNG graph export and JSON acquisition/diagnostic export.
- Unlock the instrument keypad while pausing PC acquisition polling.
- Demo mode and replay of saved acquisitions.

## Status and limitations

This initial release is experimental. Complete 4,000-sample CH1 acquisitions have been observed on a DSO-1084F running firmware 1.1.2 and 2.0.0. Other channel combinations and the new remote-control actions still require physical instrument validation. Automated tests cover the protocol, control sequencing and profiles with simulated instruments; they do not replace hardware testing.

**The graph displays signed 8-bit sample codes, not calibrated volts.** V/div, offset and trigger level in the control form are instrument settings. Time coordinates use the sample rate reported by the instrument. Refresh rate depends on the instrument, record length and USB transfer speed.

Linux is required. The local HTTP server listens only on `127.0.0.1`. This is an independent community project.

## Getting started

Requirements: Linux, Python 3.10 or later, a modern browser, and the DSO-1084F connected through its rear USB device port. Close other programs accessing the instrument.

Download the project folder and run these commands inside it:

```bash
sudo modprobe usbtmc
ls -l /dev/usbtmc*
python3 voltcraft_live.py
```

The browser opens `http://127.0.0.1:8765`. Exit with **Ctrl+C in the terminal**.

If access to `/dev/usbtmc0` is denied, set its owner for the current connection:

```bash
sudo chown "$USER" /dev/usbtmc0
python3 voltcraft_live.py
```

This permission may reset when reconnecting the device. If several USBTMC devices are present, select the correct path with `--device`. The application itself does not require root privileges.

Try it without an instrument:

```bash
python3 voltcraft_live.py --demo
```

## Controls and profiles

Checkboxes above the graph hide or show channels present in the current acquisition. A disabled channel marked “keine Messdaten” has no received samples. Enable it on the instrument or through the remote-control form.

“Vom Oszi einlesen” reads instrument settings into the form. “Einstellungen anwenden” applies the entered values; blank fields are skipped. Selecting a profile fills the form and restores the display zoom. It does not apply the settings until you click the apply button.

Profiles are stored in `~/.config/voltcraft-live/profiles.json` by default. Saving under an existing name requires the overwrite checkbox. Corrupt profile files are not silently replaced.

“Übertragung pausieren” pauses PC acquisition polling. “Bedienfeld entsperren” pauses polling and sends the unlock command last. You can then use the instrument keypad and resume PC polling when ready. Further USB queries may lock the keypad again due to instrument firmware behavior.

## Command-line options

```bash
python3 voltcraft_live.py --help
python3 voltcraft_live.py --device /dev/usbtmc1 --interval 0.5
python3 voltcraft_live.py --command legacy
python3 voltcraft_live.py --no-browser --port 8766
python3 voltcraft_live.py --replay acquisition.json
python3 voltcraft_live.py --profiles custom-profiles.json
python3 voltcraft_live.py --version
```

The default `--command auto` tries the display query and falls back to the public ALL query on a valid empty response. `legacy` selects the ALL query directly and does not require old firmware. `--interval` accepts 0.1–60 seconds between acquisitions. Demo and replay do not access USB.

## Development and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and bug reports, [docs/PROTOCOL.md](docs/PROTOCOL.md) for protocol assumptions, and [CHANGELOG.md](CHANGELOG.md) for release notes. Exported diagnostics may include the instrument serial number and measurement data. Review and redact them before posting publicly. Tests use synthetic data.

Copyright © 2026 Voltcraft Live contributors. Licensed under the **GNU General Public License, version 3 or any later version** (`GPL-3.0-or-later`), without warranty. See [LICENSE](LICENSE) for the complete terms.

Protocol research: [smooker/hantek_dso](https://github.com/smooker/hantek_dso) and [WiZZteXX/DSO4xx4c](https://github.com/WiZZteXX/DSO4xx4c). Vendor resources: [Voltcraft Download](https://voltcraftdownload.info/). These are not runtime dependencies. Vendor firmware and proprietary Windows software are not included.
