.PHONY: up down logs ps test chaos demo clean rebuild

# Bring the full stack up (api + worker + sweeper + redis + prometheus + grafana)
up:
	docker compose up -d --build

down:
	docker compose down

# Forced rebuild — useful when only a dependency changed
rebuild:
	docker compose build --no-cache

logs:
	docker compose logs -f api worker sweeper

ps:
	docker compose ps

# Unit + integration tests (skips chaos by default)
test:
	docker compose exec -T api pytest -q

# End-to-end recovery proof against the live stack
chaos:
	./scripts/chaos.sh

# Submit a Bell-state task and tail it to completion via the API
demo:
	@curl -s -X POST http://localhost:8000/tasks \
	  -H 'content-type: application/json' \
	  -d '{"qc":"OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nbit[2] c;\nh q[0];\ncx q[0], q[1];\nc[0] = measure q[0];\nc[1] = measure q[1];\n","shots":1024}' \
	  | tee /tmp/_demo.json \
	  | python3 -m json.tool
	@TID=$$(python3 -c "import json; print(json.load(open('/tmp/_demo.json'))['task_id'])"); \
	 echo; echo "Polling /tasks/$$TID …"; \
	 for i in 1 2 3 4 5 6 7 8 9 10; do \
	   echo "  [$$i] $$(curl -s http://localhost:8000/tasks/$$TID)"; \
	   curl -s http://localhost:8000/tasks/$$TID | grep -q completed && break; \
	   sleep 0.3; \
	 done

# Volumes too — full reset
clean:
	docker compose down -v
