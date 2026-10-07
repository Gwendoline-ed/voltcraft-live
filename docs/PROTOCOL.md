# Protocol notes

These notes describe the assumptions used by this implementation. They are observations of the DSO-1084F/Hantek-family protocol, not a complete vendor specification. Firmware revisions or related models may behave differently.

## USB transport and waveform packets

Linux exposes the instrument through the `usbtmc` kernel driver, usually as `/dev/usbtmc0`. Commands are ASCII strings terminated by a newline. The application serializes complete waveform reads and remote-control operations through one worker.

The waveform queries are `:WAVeform:DATA:DISP?` and `:WAVeform:DATA:ALL?`. Automatic selection falls back to ALL only after a valid empty-data response, not after a corrupt or incomplete packet.

Observed packets begin with `#9` and a nine-digit decimal packet length. On this device that length describes the complete packet, including its wrapper; generic SCPI definite-block handling would therefore be insufficient. The following fields normally carry nine-digit total-frame length and nine-digit payload offset, giving a 29-byte wrapper. One observed display-mode initial packet places run/trigger flags before the length fields. The parser accepts that variation only when the metadata validates.

The empty-data sentinel is `#9000000000`. A complete waveform is retrieved by repeating the same query until the reported offsets cover the entire frame. The application rejects gaps, duplicate/out-of-order packets, changing total lengths and implausible sizes. It can finish a transfer left pending by another client before beginning a fresh record.

## Frame and display

The frame starts with 99 bytes of ASCII metadata, followed by channel sample blocks in ascending enabled-channel order. The metadata includes run/trigger flags, reported channel settings, a four-channel enable mask, sample rate, sampling factor and time fields.

Samples are interpreted as signed 8-bit codes. The sample spacing is `sampling_factor / sample_rate`; the first displayed sample is time zero. Reported scale fields on some firmware are denormal floating-point strings whose bit patterns encode integer microvolt settings. Remote-control readback decodes that observed form when applicable. The graph deliberately remains labeled in raw sample codes until its voltage calibration is verified.

Records of up to 64,000 samples per channel retain every sample for mouse zoom. Larger accepted records use extrema-preserving buckets. The decoder supports four channel blocks, but physical validation so far covers CH1 acquisitions only.

## Remote-control sequencing

Run uses `:RUNning ON`, stop uses `:STOP`, single acquisition sets `:TRIGger:SWEep SINGle` and starts running, Auto Scale uses `:AUToset`, and force trigger uses `:TRIGger:FORCe`. The short `:SINGle` command is not used: it appeared to be a stub in the firmware inspected during development.

Settings are validated before writes and read back afterward. A mismatch is reported rather than treated as a successful change. Hardware writes are not automatically retried after a connection failure, and pending control jobs are discarded when the connection fails.

The inspected firmware locks its front-panel keypad when processing most SCPI commands. After a complete USB transaction, the application writes `:SYSTem:LOCKed OFF`. Querying `:SYSTem:LOCKed?` afterward would itself lock the panel again. The explicit unlock action therefore pauses acquisition polling, sends the unlock write last, and does not verify it with that query. Subsequent acquisition queries may lock the panel temporarily again.

## Evidence and validation limits

User-provided DSO-1084F records showed complete 99-byte metadata plus 4,000 CH1 samples on firmware 1.1.2 and 2.0.0. The included synthetic tests reproduce packet layouts without publishing private measurement files or instrument identities. New remote-control behavior is covered by simulated USB and UI tests and still requires physical validation.

Related research: [smooker/hantek_dso](https://github.com/smooker/hantek_dso), [WiZZteXX/DSO4xx4c](https://github.com/WiZZteXX/DSO4xx4c), and [Voltcraft Download](https://voltcraftdownload.info/). No vendor firmware is distributed here.
