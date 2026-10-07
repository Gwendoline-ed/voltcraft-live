# Changelog

## 0.1.0 — 2026-10-06

Initial public-source package, based on the locally used v4 application.

- Linux USBTMC waveform acquisition and browser Canvas display.
- Complete multi-packet records, empty-response fallback and pending-transfer recovery.
- Mouse-wheel zoom, amplitude zoom, panning, and four-channel visibility controls.
- Serialized remote-control queue, settings validation and readback checks.
- Persistent editable profiles with atomic writes, import/export and overwrite protection.
- Keypad unlock ends the USB transaction and pauses acquisition polling.
- PNG and JSON export, demo mode, and saved-capture replay.
- German and English documentation, GNU GPLv3-or-later notices, and automated tests.

Physical validation: complete 4,000-sample CH1 captures observed on DSO-1084F firmware 1.1.2 and 2.0.0. Remote controls and other channel combinations remain experimental. Graph amplitudes are signed sample codes; voltage calibration is not yet implemented.
