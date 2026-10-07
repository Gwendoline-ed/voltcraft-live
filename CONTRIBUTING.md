# Contributing

Bug reports, instrument compatibility reports and patches are welcome. Please include the model, firmware version, Linux distribution, Python version, command used to start the program, and steps to reproduce the issue. Distinguish physical instrument observations from demo behavior.

Capture exports can contain instrument serial numbers and measurement data. Review them before attaching them to a public issue. Do not upload login credentials, private profiles, vendor firmware or proprietary software.

## Running tests

From the project root, using Python 3.10 or later:

```bash
python3 -m unittest discover -s tests -v
```

The optional UI test uses Node.js and a native Canvas implementation only during development:

```bash
npm install --ignore-scripts
npm test
```

Set the `PYTHON` environment variable if the desired interpreter is not named `python3`. Running the application itself does not need npm or any third-party Python packages.

The UI test executes the embedded JavaScript in a simulated DOM with a native Canvas. It covers zoom/pan, channel visibility, form values, profiles and remote-control requests. It is not a full browser or physical USB test. The Python tests use synthetic protocol packets and simulated USB devices.

## Changes

Preserve the Python standard-library-only application, local server binding, serialized USB transactions, explicit profile overwrite protection, and raw-amplitude labeling. Add regression tests for packet parsing and device-write sequencing where behavior changes. Do not retry hardware writes automatically after a connection failure.

Keep user-facing documentation consistent with what has actually been confirmed on hardware. New remote commands should include their firmware/protocol source and a reproducible hardware validation report when available.

Contributions to this project are made under GNU GPLv3 or later. Preserve existing copyright and license notices, and add appropriate notices for new files.
