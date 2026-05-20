# Serplex macOS build через GitHub Actions

В репозитории есть отдельный workflow: `.github/workflows/serplex-macos.yml`.
Он собирает настоящие Tauri `.app` артефакты на macOS runner:

- `macos-14` для Apple Silicon (`arm64`)
- `macos-13` для Intel (`x64`)

## 1. Загрузка проекта на GitHub

Перед пушем проверьте, что в репозиторий не попадают секреты:

```powershell
git status --short --ignored
```

В `.gitignore` уже закрыты локальные ключи, runtime-секреты, WireGuard-конфиги, логи, `dist`, `node_modules`, Tauri `target` и временные build-папки.

Если репозиторий ещё не создан:

```powershell
git init
git add .
git commit -m "Prepare Serplex Tauri macOS build"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

Если репозиторий уже есть:

```powershell
git remote add origin https://github.com/<owner>/<repo>.git
git add .
git commit -m "Add Serplex macOS GitHub Actions build"
git push -u origin main
```

## 2. Запуск сборки macOS

Откройте GitHub:

```text
<repo> -> Actions -> Serplex macOS Tauri build -> Run workflow
```

Поля:

- `version`: оставить пустым, чтобы взять версию из `app-version.json`, или указать вручную, например `0.0.17`.
- `arch`: `both`, `arm64` или `x64`.

После завершения workflow скачать artifacts:

- `serplex-macos-arm64`
- `serplex-macos-x64`

Внутри будут архивы вида:

```text
Serplex_macos_arm64_0.0.x.tar.gz
Serplex_macos_x64_0.0.x.tar.gz
```

## 3. Публикация macOS-версии на serplex.ashees.dev

Скачанные архивы положить локально в:

```text
dist\SerplexTauriArtifacts
```

Затем опубликовать подписанный манифест:

```powershell
.\Build-Publish-LocalCodexUpdate.ps1 `
  -PublishExisting `
  -ArtifactRoot .\dist\SerplexTauriArtifacts `
  -AllowMissingArtifacts `
  -Changes @("Добавлена сборка Serplex для macOS.")
```

Сайт автоматически начнёт показывать macOS в выборе ОС, если macOS-артефакт присутствует в подписанном манифесте.

## Важно про подпись Apple

Эта сборка не notarized и не подписана Apple Developer ID, если в репозиторий не добавлены Apple-сертификаты. На macOS такой `.app` может открываться только через ручное подтверждение безопасности или после снятия quarantine:

```bash
xattr -dr com.apple.quarantine Serplex.app
```

Для нормального публичного распространения нужен Apple Developer ID и отдельная настройка signing/notarization.
