# Thumb App

App Django para extrair thumbnails e metadados de vídeos usando yt-dlp.

## Funcionalidades

- 🖼️ **Extração de Thumbnail**: Baixa thumbnails em máxima qualidade
- 📊 **Metadados do Vídeo**: Título, descrição, tags, categorias, uploader, etc.
- 📝 **Legendas**: Lista e baixa legendas disponíveis

## URLs

| URL | Descrição |
|-----|-----------|
| `/thumb/` | Página principal (interface web) |
| `/thumb/api/info/` | API para obter informações do vídeo (JSON) |
| `/thumb/api/thumbnail/` | Download da thumbnail |
| `/thumb/api/download/` | Download do vídeo |
| `/thumb/api/subtitles/` | Download de legendas |

## Uso da API

### Obter informações do vídeo

```bash
GET /thumb/api/info/?url=https://www.youtube.com/watch?v=VIDEO_ID
```

### Download da thumbnail

```bash
GET /thumb/api/thumbnail/?url=https://www.youtube.com/watch?v=VIDEO_ID&quality=maxresdefault
```

Qualidades disponíveis: `maxresdefault`, `sddefault`, `hqdefault`, `mqdefault`, `default`

### Download do vídeo

```bash
POST /thumb/api/download/
Content-Type: application/x-www-form-urlencoded

url=https://www.youtube.com/watch?v=VIDEO_ID&format_id=22
```

### Download de legendas

```bash
GET /thumb/api/subtitles/?url=https://www.youtube.com/watch?v=VIDEO_ID&lang=en
```

## Resposta da API de Informações

```json
{
  "id": "VIDEO_ID",
  "title": "Título do Vídeo",
  "description": "Descrição completa...",
  "uploader": "Nome do Uploader",
  "channel": "Nome do Canal",
  "duration": 180,
  "view_count": 1000000,
  "like_count": 50000,
  "upload_date": "20240101",
  "thumbnail": "https://...",
  "thumbnails": [...],
  "subtitles": [...],
  "tags": ["tag1", "tag2"],
  "categories": ["Categoria"]
}
```

## Instalação

O app já está registrado em `INSTALLED_APPS` e as URLs estão configuradas em `ytdl/urls.py`.

Para usar, acesse: `http://localhost:8000/thumb/`
