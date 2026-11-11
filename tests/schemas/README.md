# 第三方 JSON Schema（仅测试用）

测试里用这些官方 schema 校验 OPDS 2.0 输出，不参与运行时。

| 目录 | 来源 | 版本 |
| --- | --- | --- |
| `opds/*.schema.json`（除 authentication） | https://github.com/opds-community/specs `schema/` | `8fda670` |
| `opds/authentication.schema.json` | https://github.com/opds-community/drafts `schema/` | `1402634` |
| `webpub/` | https://github.com/readium/webpub-manifest `schema/`（BSD-3-Clause，见 `webpub/LICENSE`） | `9bcf48e` |

更新时整目录替换即可，`tests/test_opds2.py` 按各文件的 `$id` 注册引用。
