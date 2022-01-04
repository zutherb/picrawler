init:
	pip install -r requirements.txt

test:
	py.test tests

infrastructure:
	ansible-playbook -i infrastructure/hosts infrastructure/picrawler.yml

.PHONY: init test
