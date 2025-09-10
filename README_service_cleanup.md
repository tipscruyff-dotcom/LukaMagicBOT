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
