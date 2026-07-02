.PHONY: deploy package-deps

deploy:
	mkdir -p ~/.claude/skills/lit-review ~/.claude/skills/lit-review-quick ~/.claude/skills/lit-review-update ~/.claude/skills/lit-review-package
	cp skills/lit-review.md ~/.claude/skills/lit-review/SKILL.md
	cp skills/lit-review-quick.md ~/.claude/skills/lit-review-quick/SKILL.md
	cp skills/lit-review-update.md ~/.claude/skills/lit-review-update/SKILL.md
	cp skills/lit-review-package.md ~/.claude/skills/lit-review-package/SKILL.md

package-deps:
	@echo "Install Pandoc + xelatex for /lit-review-package:"
	@echo "  brew install pandoc"
	@echo "  brew install --cask basictex"
	@echo "  sudo tlmgr update --self && sudo tlmgr install collection-fontsrecommended"
	@echo ""
	@echo "Verify:"
	@echo "  pandoc --version | head -1"
	@echo "  xelatex --version | head -1"
