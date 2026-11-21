# Studio HQ read-only dashboard

Open [Studio HQ](https://vnsh.tail9bdcf7.ts.net/studio-hq/) with the same Tailscale account as the Windows host. The app works in the browser on Windows, Android, and Mac. Supported browsers can also add it to the home screen or dock. It updates from the Windows host every 30 seconds.

The overview includes publishing records, manual queue status, and separate channel filters for Histold, Astrotold, and Shotsnip. Media Archive links to verified Drive videos and shows whether the original MP4 is also present locally. Operations shows the latest archive and cleanup results. Published video links open YouTube. Draft video playback is outside this version.

The Rust/Axum service listens on `127.0.0.1:8801`. Tailscale Serve maps `/studio-hq` to it alongside the existing root dashboard. Remote requests must carry the login of the allowed Tailscale account. The device must remain connected and its Windows user signed in to run the **Studio HQ Dashboard** scheduled task.

The service invokes a standard-library Python adapter to read existing JSON and SQLite records. It serves an allowlisted snapshot from `D:\StudioHQ\state\dashboard-snapshot.json` and retains the previous snapshot if refresh fails. The private runtime settings are in `D:\StudioHQ\private\hub-settings.json`. That directory is outside the served frontend.

Build the Rust service with `cargo build --locked --release --manifest-path studio_hq/hub/Cargo.toml`. In `studio_hq/ui`, run `npm ci` then `npm run build`. The deployed binary and frontend are under `D:\StudioHQ\dashboard`. Replacing the binary requires stopping only the **Studio HQ Dashboard** task, copying the build, and starting the task again. Restarting this task does not restart the archive or publishing workers.

To remove the private route while keeping the existing dashboard, run `tailscale serve --https=443 --set-path=/studio-hq off`. To stop the local Studio HQ service, stop the **Studio HQ Dashboard** scheduled task.

The initial live check verified the Windows Tailscale route, static assets, read-only API, denied unknown identities, working channel filters, and a phone-width browser layout. Physical Android and Mac clients were not available for direct testing.
