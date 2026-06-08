# TA Agent — PowerShell task runner (Windows equivalent of the Makefile)
# Usage:  ./tasks.ps1 up   |  ./tasks.ps1 dev  |  ./tasks.ps1 backend  ...
#
# LLM access uses the authenticated Claude Code CLI directly (`claude -p`) —
# there is no proxy to start. Just make sure `claude` is logged in.

param(
    [Parameter(Position = 0)]
    [string]$Task = "help"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

switch ($Task) {
    "up" {
        docker-compose up -d
    }
    "down" {
        docker-compose down
    }
    "backend" {
        Set-Location "$root/backend"
        uvicorn main:app --reload --port 8001
    }
    "frontend" {
        Set-Location "$root/frontend"
        npm run dev
    }
    "dev" {
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root/backend'; uvicorn main:app --reload --port 8001"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root/frontend'; npm run dev"
        Write-Host "Launched backend (8001) and frontend (3001) in new windows."
    }
    "worker" {
        Set-Location "$root/backend"
        celery -A tasks.celery_app worker --loglevel=info --pool=solo
    }
    "migrate" {
        Get-Content "$root/supabase/migrations/0001_init.sql" |
            docker exec -i ta-agent-db psql -U postgres -d ta_agent
    }
    "logs" { docker-compose logs -f }
    default {
        Write-Host "TA Agent task runner. Available tasks:"
        Write-Host "  up        Start Docker services (Postgres + Redis)"
        Write-Host "  down      Stop services"
        Write-Host "  dev       Start backend + frontend in new windows"
        Write-Host "  backend   Start backend only (port 8001)"
        Write-Host "  frontend  Start frontend only (port 3001)"
        Write-Host "  worker    Start Celery worker"
        Write-Host "  migrate   Apply DB migrations"
        Write-Host "  logs      Tail docker logs"
    }
}
