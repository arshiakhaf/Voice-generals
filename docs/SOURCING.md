# چطور مطمئن می‌شویم صدا روی فایل درست می‌نشیند

این پروژه روی «حافظه» تکیه نمی‌کند؛ همه‌چیز از دادهٔ خود بازی استخراج می‌شود.

## منبع ۱ — لیست فایل‌های صوتی

`Voice.ini` و `SoundEffects.ini` نسخهٔ اصلی Zero Hour (از ریپوی
`TheSuperHackers/GeneralsGamePatch`، شاخهٔ `GameFilesOriginalZH` — همان فایل‌هایی که
پچ رسمی روی آن‌ها سوار می‌شود).

هر بلوک `AudioEvent` اسم فایل‌های wav را می‌دهد، مثلاً:

```ini
AudioEvent DozerUSAVoiceSelect
  Sounds = vdousea vdouseb vdousec vdoused vdousee vdousef vdouseg vdouseh vdousei
  Control = random
  Volume = 90
  Type = ui voice player
End
```

## منبع ۲ — کدام آبجکت کدام رویداد را می‌خواند

`Object/*.ini` (۴۴ فایل). برای هر آبجکت، اسلات‌هایی مثل `VoiceSelect`، `VoiceMove`،
`VoiceBuildResponse`، `VoiceNoBuild`، `VoiceTaskComplete`، `VoiceCrush`، `VoiceRepair`،
`VoiceDisarm` استخراج می‌شود.

نتیجه برای بولدوزر آمریکا:

| آبجکت | فایل | رویدادها |
|---|---|---|
| `AmericaVehicleDozer` | `AmericaVehicle.ini` | `DozerUSAVoice*` |
| `Lazr_AmericaVehicleDozer` | `LaserGeneral.ini` | `DozerUSAVoice*` (همان) |
| `AirF_AmericaVehicleDozer` | `AirforceGeneral.ini` | `DozerUSAVoice*` (همان) |
| `SupW_AmericaVehicleDozer` | `SuperWeaponGeneral.ini` | `DozerUSAVoice*` (همان) |
| `Boss_VehicleDozer` | `BossGeneral.ini` | `DozerChinaVoice*` (بولدوزر چین!) |

یعنی یک پک برای هر چهار ژنرال آمریکا کافی است، ولی باس‌جنرال صدای چینی می‌خواهد.

## منبع ۳ — چطور موتور فایل را پیدا می‌کند

از سورس GPL بازی:

* `AudioEventRTS::generateFilenamePrefix()` → `Data\Audio\Sounds\`
* `AudioEventRTS::generateFilenameExtension()` → `.<SoundsExtension>` که در
  `AudioSettings.ini` مقدار `wav` دارد
* `FileSystem::openFile()` → اول `TheLocalFileSystem`، بعد `TheArchiveFileSystem`
* `BIGFileSystem::loadArchiveFilesFromDirectory()` → هر `*.big` در ریشهٔ بازی
* `parseMod()` → `-mod <file.big>` یا `-mod <dir>` با اولویت بالاتر

پس اسم فایل تنها چیزی است که به آن نیاز داریم.

## تأیید متقابل

لیست رویدادها با دو منبع مستقل مقایسه شد و **۰ اختلاف** داشت:

1. `TheSuperHackers/GeneralsGamePatch` → `GameFilesOriginalZH/Data/INI/Voice.ini`
2. میرور `bill-rich/cncstats` → `inizh/Data/INI/Voice.ini`
3. `petrock6/ini` → `Voice.ini`

## بازسازی

```bash
python3 tools/fetch_vanilla.py && python3 tools/build_data.py
python3 tools/build_pack.py data/packs/usa_dozer.json --list
```

اسکریپت دوم هر فایل پک را با لیست اصلی مقایسه می‌کند و اگر یکی کم یا زیاد باشد
`PROBLEMS` چاپ می‌کند.
