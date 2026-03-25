# Variáveis
DC = docker-compose
DC_LOCAL = $(DC) -f docker-compose.local.yml
APP_CONTAINER = yii2-demo-app

.PHONY: up down restart build logs shell fresh seed-epic help restart-local

## up: Inicia os contentores em background
up:
	$(DC) up -d

## down: Pára e remove os contentores
down:
	$(DC) down

## build: Reconstrói as imagens (sem cache para evitar erros de extensão)
build:
	$(DC) build --no-cache

## restart: Reinicia os serviços (padrão)
restart: down up

## restart-local: Reinicia os serviços usando o docker-compose.local.yml
restart-local:
	$(DC_LOCAL) down
	$(DC_LOCAL) up -d
	@echo "Serviços locais reiniciados com sucesso!"

## logs: Mostra os logs do contentor PHP em tempo real
logs:
	$(DC) logs -f $(APP_CONTAINER)

## shell: Entra no terminal do contentor PHP
shell:
	docker exec -it $(APP_CONTAINER) sh

## fresh: Limpa a base de dados e recria a estrutura (Migrate Fresh)
fresh:
	docker exec -it $(APP_CONTAINER) php yii migrate/fresh --interactive=0

## seed-epic: Aplica o teu seeder do Super-Homem e Zeus
seed-epic:
	docker exec -it $(APP_CONTAINER) php yii migrate --interactive=0

## demo-reset: O comando mestre para a tua apresentação (Limpa e Poe os dados épicos)
demo-reset: fresh seed-epic
	@echo "Base de dados limpa e personagens épicos inseridos com sucesso!"

## help: Mostra esta ajuda
help:
	@echo "Comandos disponíveis:"
	@sed -n 's/^##//p' Makefile | column -t -s ':' |  sed -e 's/^/ /'