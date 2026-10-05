# Android app

The Android build of the Flutter app, distributed as a sideloaded APK from S3
(`Terraform/s3-mobile-releases.tf`) through CloudFront's `/app/*` path, which
answers Android user agents only.

## Build locally

```sh
flutter build apk --release --target-platform android-arm64 \
  --dart-define-from-file=config/dev.json --dart-define=ANDROID_APP=true
```

`ANDROID_APP=true` is what makes the build run as the app (rolling 30-day
session, background checks, in-app updates). Without `android/key.properties`
the release build is signed with the debug key and can't update an installed
release build.

## Release signing (one-time)

Every release must be signed with the same key. A lost key means every user
has to uninstall and reinstall, so keep an offline backup.

1. Generate the key (JDK `keytool`, e.g. Android Studio's `jbr/bin/keytool`):

   ```sh
   keytool -genkeypair -v -keystore sports-predictor-release.jks \
     -alias sports-predictor -keyalg RSA -keysize 4096 -validity 10000
   ```

2. Add three repository secrets and one variable (GitHub → Settings → Secrets and variables → Actions):

   | Name | Kind | Value |
   | --- | --- | --- |
   | `ANDROID_KEYSTORE_BASE64` | secret | `base64 -w0 sports-predictor-release.jks` |
   | `ANDROID_KEYSTORE_PASSWORD` | secret | the keystore password |
   | `ANDROID_KEY_PASSWORD` | secret | the key password (the same as the keystore's for a PKCS12 keystore) |
   | `ANDROID_KEY_ALIAS` | variable | `sports-predictor` |

`mobile_hosting.yml` writes these to `android/app/release.jks` and
`android/key.properties` (both gitignored) before building.

## Releases

`mobile_deploy.yml` (push to `mobile/**`) and `app_deploy.yml` (push to
`main`) build and publish.

- **versionCode** is one more than the published APK's `version-code`
  metadata.
- **versionName** is `<major>.<minor>.<versionCode>`. Major and minor come
  from `pubspec.yaml`'s `version:` and are bumped by hand for bigger releases.
- **A build is skipped** unless the files that make up the app changed since
  the published APK: `lib/`, `android/` (Markdown aside), `pubspec.yaml`,
  `pubspec.lock`, or the rendered config. Tests, docs, web-only files and the
  backend never publish an update.
- **Release notes** shown to users come from `../RELEASE_NOTES.txt`: one short
  line about features or fixes, no technical detail. Update it with every
  change that affects the app.

## Dependency lockfile

Release builds resolve exactly the versions pinned in `app/gradle.lockfile`
(debug builds for local phones and emulators are not locked). After adding or
upgrading a plugin, or changing the Flutter version, regenerate it or the
release build fails on the mismatch:

```sh
cd android
./gradlew :app:dependencies --write-locks -Ptarget-platform=android-arm64
```
