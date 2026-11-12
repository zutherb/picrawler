# PiCrawler for iPhone and iPad

Native SwiftUI remote control in the look of the web UI's Remote page:
camera stream, joystick (with haptics), speed, actions and a big STOP.
Side by side on iPad and in landscape, stacked on iPhone in portrait.

## Finding the robot

1. **Bonjour**: the Pi announces `_picrawler._tcp` via avahi (set up by
   Ansible). The app lists every robot on the network; with exactly one
   robot and a saved login it connects on its own.
2. **Hotspot**: away from home the Pi opens `PiCrawler-<id>`. If the
   phone is already on it, the app uses `10.42.0.1` directly. Otherwise
   "Join PiCrawler-1" asks iOS to join (password saved in the keychain).
3. Manual address as a last resort.

## Security

- Same login as the browser; the user needs the `admin` role to drive.
  Login is stored in the keychain if you choose "Remember".
- The robot's certificate is self-signed. The app pins its SHA-256
  fingerprint on the first connection and refuses a different one later
  ("Forget this robot" resets it).

## Building

Needs Xcode 15 or newer and [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```bash
brew install xcodegen
cd apps/ios
xcodegen            # creates PiCrawler.xcodeproj from project.yml
open PiCrawler.xcodeproj
```

Set your team under Signing & Capabilities. The two capabilities in
`PiCrawler/Support/PiCrawler.entitlements` ("Hotspot Configuration",
"Access Wi-Fi Information") need a paid developer account; without them,
remove the entitlements file and join the hotspot in the iOS settings
instead. Everything else works the same.

## Status

The network code (login, CSRF, remote, server-sent events, MJPEG frames,
stop) was tested from the command line against the web service's mock.
The app as a whole typechecks against the macOS SDK; it has not been built
or run on an iOS device or simulator yet (no Xcode on the machine it was
written on).
