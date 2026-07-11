.PHONY: api ui tick loop worker test backend-test frontend-build docker

api:
	cd backend && consciousness-api

ui:
	cd frontend && npm run dev

tick:
	cd backend && consciousness-tick

loop:
	cd backend && consciousness-loop

worker:
	cd backend && consciousness-worker

backend-test:
	cd backend && pytest

frontend-build:
	cd frontend && npm run build

test: backend-test frontend-build

docker:
	docker compose up --build
