// 按需引入 highlight.js（P1 前端瘦身）
// 全量 `import hljs from 'highlight.js'` 会把 ~1MB 语言包打进 bundle；
// 改为只注册常用语言，显著缩小 Chat/ServiceDetail 等 chunk。
import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/github-dark.css'

import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import sql from 'highlight.js/lib/languages/sql'
import cpp from 'highlight.js/lib/languages/cpp'
import c from 'highlight.js/lib/languages/c'
import csharp from 'highlight.js/lib/languages/csharp'
import java from 'highlight.js/lib/languages/java'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import css from 'highlight.js/lib/languages/css'
import scss from 'highlight.js/lib/languages/scss'
import ini from 'highlight.js/lib/languages/ini'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import diff from 'highlight.js/lib/languages/diff'
import plaintext from 'highlight.js/lib/languages/plaintext'
import powershell from 'highlight.js/lib/languages/powershell'
import ruby from 'highlight.js/lib/languages/ruby'
import php from 'highlight.js/lib/languages/php'
import swift from 'highlight.js/lib/languages/swift'
import kotlin from 'highlight.js/lib/languages/kotlin'
import makefile from 'highlight.js/lib/languages/makefile'
import nginx from 'highlight.js/lib/languages/nginx'
import lua from 'highlight.js/lib/languages/lua'

const langs = {
  javascript, js: javascript,
  typescript, ts: typescript,
  python, py: python,
  bash, shell: bash, sh: bash,
  json,
  xml, html: xml,
  yaml, yml: yaml,
  markdown, md: markdown,
  sql,
  cpp, cc: cpp, cxx: cpp,
  c,
  csharp, cs: csharp,
  java,
  go,
  rust, rs: rust,
  css,
  scss, less: scss,
  ini, toml: ini,
  dockerfile,
  diff,
  plaintext, text: plaintext,
  powershell, ps1: powershell,
  ruby, rb: ruby,
  php,
  swift,
  kotlin, kt: kotlin,
  makefile, make: makefile,
  nginx,
  lua,
}

for (const [name, def] of Object.entries(langs)) {
  if (def && !hljs.getLanguage(name)) hljs.registerLanguage(name, def)
}

export default hljs