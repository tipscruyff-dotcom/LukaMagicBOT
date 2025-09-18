# LukaMagicBOT - Ferramentas de Limpeza de Mensagens de Serviço (Versão 2.0)

Este documento descreve as ferramentas avançadas criadas para monitorar, analisar, visualizar e simular o sistema de limpeza automática de mensagens de serviço no LukaMagicBOT.

## Visão Geral das Ferramentas

### 1. `service_cleanup_monitor.py`
Ferramenta para monitoramento periódico do sistema de limpeza de mensagens de serviço, com alertas por e-mail.

**Recursos:**
- Monitoramento contínuo ou sob demanda
- Verificação da taxa de sucesso das exclusões
- Sistema de alerta baseado em limiar configurável
- Notificação por e-mail em caso de falhas
- Exportação de resultados para JSON

**Uso básico:**
```bash
# Verificação única das últimas 24 horas
python tools/service_cleanup_monitor.py

# Verificação contínua a cada 1 hora com alertas por e-mail
python tools/service_cleanup_monitor.py --continuous --interval 3600 --alert-email --email-to admin@example.com

# Verificação personalizada com exportação de resultados
python tools/service_cleanup_monitor.py --hours 12 --threshold 0.9 --output results.json
```

### 2. `service_cleanup_logger.py`
Ferramenta para registro em tempo real das operações de limpeza de mensagens de serviço com estatísticas detalhadas.

**Recursos:**
- Modo "tail" para monitoramento em tempo real
- Exibição colorida dos eventos (com coloredlogs)
- Estatísticas detalhadas por chat e tipo de erro
- Exportação de logs para JSON

**Uso básico:**
```bash
# Visualizar atividade recente (últimos 15 minutos)
python tools/service_cleanup_logger.py

# Monitorar novos eventos em tempo real
python tools/service_cleanup_logger.py --tail

# Visualizar atividade dos últimos 30 minutos e exportar para JSON
python tools/service_cleanup_logger.py --minutes 30 --output activity_log.json
```

### 3. `service_cleanup_viewer.py`
Ferramenta para visualização e análise de dados históricos do sistema de limpeza de mensagens.

**Recursos:**
- Visualização gráfica dos dados de limpeza
- Relatórios de taxa de sucesso ao longo do tempo
- Análise por chat e tipo de mensagem
- Distribuição de erros e problemas comuns
- Exportação de dados para análise externa

**Uso básico:**
```bash
# Visualizar resumo dos últimos 30 dias
python tools/service_cleanup_viewer.py

# Gerar visualizações gráficas
python tools/service_cleanup_viewer.py --visualize

# Analisar dados de um chat específico
python tools/service_cleanup_viewer.py --chat 123456789 --days 14

# Exportar dados para análise externa
python tools/service_cleanup_viewer.py --days 90 --export cleanup_data.json
```

**Requisitos adicionais:**
Para as visualizações, instale as dependências:
```bash
pip install pandas matplotlib
```

### 4. `service_cleanup_simulator.py`
Ferramenta para simular mensagens de serviço e testar a funcionalidade de limpeza.

**Recursos:**
- Simulação de vários tipos de mensagens de serviço
- Simulação em diferentes tipos de chats
- Avaliação da taxa de detecção e exclusão
- Testes de robustez com nomes de usuário complexos
- Exportação de resultados para análise

**Uso básico:**
```bash
# Executar simulação completa
python tools/service_cleanup_simulator.py

# Simular apenas mensagens de entrada padrão
python tools/service_cleanup_simulator.py --template join_standard --messages 20

# Simular em grupos normais (não supergrupos)
python tools/service_cleanup_simulator.py --chat-type group --export sim_results.json
```

## Exemplos de Fluxos de Trabalho

### Monitoramento Contínuo
Para configurar um monitoramento contínuo do sistema:

1. Configure o monitor como um serviço ou tarefa agendada:
   ```bash
   python tools/service_cleanup_monitor.py --continuous --interval 3600 --alert-email --email-to admin@example.com
   ```

2. Use o logger para diagnóstico em tempo real quando necessário:
   ```bash
   python tools/service_cleanup_logger.py --tail
   ```

### Análise de Problemas
Se estiver ocorrendo falhas no sistema de limpeza:

1. Verifique o status atual:
   ```bash
   python tools/service_cleanup_monitor.py --hours 6
   ```

2. Analise os padrões históricos:
   ```bash
   python tools/service_cleanup_viewer.py --visualize --days 7
   ```

3. Execute simulações para verificar tipos específicos de mensagens:
   ```bash
   python tools/service_cleanup_simulator.py --template join_custom_names
   ```

### Relatórios Periódicos
Para gerar relatórios periódicos:

1. Exporte os dados de um período específico:
   ```bash
   python tools/service_cleanup_viewer.py --days 30 --export monthly_report.json
   ```

2. Gere visualizações avançadas para análise:
   ```bash
   python tools/service_cleanup_viewer.py --days 30 --advanced
   ```

## Configuração de Variáveis de Ambiente

Para usar alertas por e-mail no monitor, configure as seguintes variáveis de ambiente:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USERNAME=user
export SMTP_PASSWORD=password
export SMTP_FROM=bot@example.com
```

## Notas Adicionais

- Todas as ferramentas devem ser executadas a partir do diretório raiz do projeto.
- As ferramentas de visualização requerem permissões de leitura no banco de dados.
- As ferramentas de monitoramento são projetadas para funcionar tanto em ambientes de desenvolvimento quanto de produção.

## Resolução de Problemas

### Problemas Comuns

1. **Erro "Failed to import required modules"**
   - Solução: Execute as ferramentas a partir do diretório raiz do projeto.

2. **Visualizações não aparecem**
   - Solução: Certifique-se de ter instalado pandas e matplotlib (`pip install pandas matplotlib`).

3. **Erros de conexão ao banco de dados**
   - Solução: Verifique se as configurações de conexão estão corretas e se o banco de dados está acessível.

4. **Alertas de e-mail não funcionam**
   - Solução: Verifique as variáveis de ambiente SMTP e teste a conexão com o servidor de e-mail.

## Contribuindo

Para contribuir com melhorias para estas ferramentas:

1. Faça fork do repositório
2. Crie uma branch para sua funcionalidade (`git checkout -b feature/nova-funcionalidade`)
3. Faça commit de suas alterações (`git commit -am 'Adiciona nova funcionalidade'`)
4. Faça push para a branch (`git push origin feature/nova-funcionalidade`)
5. Crie um novo Pull Request