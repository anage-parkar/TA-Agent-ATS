# TA Agent — local dev task runner
# On Windows without `make`, use ./tasks.ps1 (same commands) instead.
#
# LLM access uses the authenticated Claude Code CLI directly (`claude -p`) —
# there is no proxy to start. Just make sure `claude` is logged in.

.PHONY: help up down dev backend frontend migrate worker logs

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

up:             ## Start local services (Postgres + Redis)
	docker-compose up -d

down:           ## Stop local services
	docker-compose down

dev:            ## Start backend + frontend dev servers
	cd backend && uvicorn main:app --reload --port 8001 &
	cd frontend && npm run dev

backend:        ## Start backend only
	cd backend && uvicorn main:app --reload --port 8001

frontend:       ## Start frontend only
	cd frontend && npm run dev

worker:         ## Start Celery worker
	cd backend && celery -A tasks.celery_app worker --loglevel=info

migrate:        ## Apply DB migrations to running Postgres
	docker exec -i ta-agent-db psql -U postgres -d ta_agent < supabase/migrations/0001_init.sql

logs:           ## Tail docker logs
	docker-compose logs -f
