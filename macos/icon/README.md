# macOS App icon

`CourseBackup.icns` is the shipping icon. The ten PNGs in `CourseBackup.iconset`
include 16, 32, 128, 256, and 512 point masters at 1× / 2× (up to 1024px).
Small masters have individually adjusted geometry and pixel-aligned stems;
Retina variants are rendered directly from vector paths, never resized PNGs.

To regenerate on macOS with Apple Command Line Tools:

```sh
xcrun swiftc macos/icon/RenderIcon.swift -o /tmp/course-backup-icon-renderer
/tmp/course-backup-icon-renderer macos/icon/CourseBackup.iconset
iconutil -c icns macos/icon/CourseBackup.iconset -o macos/icon/CourseBackup.icns
```

The App builder copies the icon into `Contents/Resources` and sets
`CFBundleIconFile` / `CFBundleIconName` before signing. The icon contains no text,
YouTube logo, or personal data. This change does not alter App or backend behavior.
