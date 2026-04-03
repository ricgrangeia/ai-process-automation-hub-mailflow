# Variáveis
DC = docker-compose
DC_LOCAL = $(DC) -f docker-compose.local.yml
AI_WORKER = ai-worker
EMAIL_WORKER = email-worker
DASHBOARD = dashboard

.PHONY: up down restart build logs shell help restart-local

## up: Inicia os contentores em background
up:
	$(DC) up -d

## down: Pára e remove os contentores
down:
	$(DC) down

## build: Reconstrói as imagens (sem cache)
build:
	$(DC) build --no-cache

## restart: Reinicia os serviços (padrão)
restart: down up

## restart-local: Reinicia os serviços usando o docker-compose.local.yml
restart-local:
	$(DC_LOCAL) down
	$(DC_LOCAL) up -d
	@echo "Serviços locais reiniciados com sucesso!"

## logs: Mostra os logs de todos os serviços em tempo real
logs:
	$(DC) logs -f

## logs-ai: Mostra os logs do ai-worker
logs-ai:
	$(DC) logs -f $(AI_WORKER)

## logs-email: Mostra os logs do email-worker
logs-email:
	$(DC) logs -f $(EMAIL_WORKER)

## shell: Entra no terminal do ai-worker
shell:
	docker exec -it $(AI_WORKER) sh

## help: Mostra esta ajuda
help:
	@echo "Comandos disponíveis:"
	@sed -n 's/^##//p' Makefile | column -t -s ':' |  sed -e 's/^/ /'