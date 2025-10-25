Limpeza de mensagens de serviço (entrada/saída)

Visão geral
- Recurso opcional para encaminhar e remover automaticamente mensagens de serviço do Telegram relacionadas a entradas e saídas do grupo.
- Desligado por padrão — nada muda até você ativar.

Onde a configuração é salva
- As configurações ficam na tabela `settings` (criada automaticamente). Chaves principais e valores padrão:
  - `service_cleanup.enabled`: false (desligado)
  - `service_cleanup.remove_on_join`: true
  - `service_cleanup.remove_on_leave`: true
  - `service_cleanup.forward_target_id`: "" (vazio)

Como usar (rápido)
1. Entre no painel Admin → Service Cleanup.
2. Ative o recurso (Enable) se quiser que o bot atue.
3. Opcional: preencha `Forward target ID` com o chat/usuário onde deseja receber os eventos.
4. Salve. As mudanças têm efeito imediato.

Permissões necessárias
- Para remover mensagens no grupo, o bot precisa ser administrador do grupo e ter a permissão "Delete messages".
- Se o bot não tiver permissão de exclusão, ele ainda tentará encaminhar ou enviar um resumo para o `forward_target_id`, e ficará registrado nos logs. A exclusão será ignorada sem causar falha.

Notas importantes
- Apenas mensagens de serviço de entrada/saída são afetadas (ex.: `new_chat_members`, `left_chat_member`, ou mudanças de `chat_member` com status `left`/`kicked`).
- O bot não remove outros tipos de mensagens (pin, alterações de título, mensagens de usuários, etc.).
- O comportamento padrão é compatível com versões anteriores: com `enabled=false` nada é removido.

Dicas rápidas
- Use um chat privado ou canal como `forward_target_id` para centralizar logs.
- Teste em um grupo de staging antes de ativar em produção.

Se precisar, posso adicionar métricas simples (contadores) ou testes adicionais para os caminhos de fallback.

Limitações conhecidas
- O Telegram Bot API não permite ler o histórico de mensagens. O bot só apaga mensagens de serviço que ele vê quando chegam (ou que foram registradas depois que o recurso foi instalado).
- Para apagar mensagens antigas que o bot nunca viu, use o script opcional abaixo (userbot / Telethon).

Limpeza retroativa com Userbot (opcional)
Use este utilitário apenas se você quiser remover mensagens antigas de serviço que o bot não consegue ver via Bot API. Ele usa a sua conta (Telethon).

1) Requisitos
- Obtenha `TG_API_ID` e `TG_API_HASH` em https://my.telegram.org/apps
- Opcional: gere uma StringSession (`TG_SESSION`) previamente para evitar login interativo.

2) Variáveis de ambiente (exemplos)
```
TG_API_ID=123456
TG_API_HASH=abcd1234abcd1234abcd1234abcd1234
TG_SESSION= # opcional (StringSession)
TARGET_CHAT_ID=-1001234567890 # ou use TARGET_CHAT_USERNAME=@meugrupo
RC_SINCE=2024-01-01T00:00:00Z # opcional
RC_UNTIL= # opcional
RC_LIMIT=5000
RC_BATCH=100
```

3) Executando
```
python tools/retro_cleanup_userbot.py --chat "-1001234567890" --since "2024-01-01T00:00:00Z" --limit 8000 --batch 200 --dry-run
python tools/retro_cleanup_userbot.py --chat "@meugrupo" --since "2024-01-01T00:00:00Z" --limit 8000 --batch 200
```

Notas
- O script identifica mensagens de serviço (join/leave) e as apaga em lotes.
- Rodar como userbot implica riscos de segurança; não compartilhe suas credenciais e prefira usar `TG_SESSION`.
