# Third-party notices

This release vendors only the Iconify runtime and the icon records referenced
by the application. The bundle is generated from pinned npm artifacts and does
not contact an icon CDN at runtime.

| Package | Version | License | npm integrity |
|---|---:|---|---|
| `iconify-icon` | 1.0.7 | MIT | `sha512-MxaO3Jhf3f5ymPWGHR9x74f90TNKcq1D+B2iGucGhVtqAgbC9EtM06kKiTGH2CKELNnexckwhrA3/+OpT4HKFw==` |
| `@iconify-json/lucide` | 1.2.84 | ISC | `sha512-m45MY1aW2swSK6Neb3J2qdZ+BxZ1VTdN7UeoXENwyMACG9bqGHWYJYxWoFzwoSnCfYLyKWTcBhesBgMd5IrUzQ==` |
| `@iconify-json/logos` | 1.2.10 | CC0-1.0 | `sha512-qxaXKJ6fu8jzTMPQdHtNxlfx6tBQ0jXRbHZIYy5Ilh8Lx9US9FsAdzZWUR8MXV8PnWTKGDFO4ZZee9VwerCyMA==` |
| `@iconify-json/mdi` | 1.2.3 | Apache-2.0 | `sha512-O3cLwbDOK7NNDf2ihaQOH5F9JglnulNDFV7WprU2dSoZu3h3cWH//h74uQAB87brHmvFVxIOkuBX2sZSzYhScg==` |
| `@iconify-json/flat-color-icons` | 1.2.2 | MIT | `sha512-VGLJRQHabHaw1cp041hPnGsZ2rIzQEvLa/UJpdEuAKPnckGnWP97t0zxio7+RWY3KthWcd1T1ucnNavBQTerWw==` |

Vendored file checksums for this release:

- `app/static/vendor/iconify/iconify-icon-1.0.7.min.js`: `a434138164926fec01831fc230fe9dc211edfd8d55d369aef1095fc98d782bbd`
- `app/static/vendor/iconify/portfolio-icon-collections-2026.07.js`: `7a04900f09d80be7bab0e1f59fd23e35e49205380b0e3e191d88f0b7c1bd3953`

The upstream license headers are retained where supplied. Full license texts
and project attribution are available from each package's published npm
artifact. `jsdom` 29.1.1 (MIT) is a development-only UI test dependency and is
not included in the release archive's runtime payload.
