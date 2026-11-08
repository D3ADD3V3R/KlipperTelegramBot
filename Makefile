.PHONY: install uninstall test

install:
	@bash scripts/install.sh

uninstall:
	@bash scripts/uninstall.sh

test:
	@python3 -m unittest discover -s tests -v
