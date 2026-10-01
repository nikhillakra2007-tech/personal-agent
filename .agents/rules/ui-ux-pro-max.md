# UI/UX Pro Max Design Intelligence

Always refer to and leverage the **UI/UX Pro Max** skill and repository for all UI/UX design, styling, color palette, typography, responsive layouts, accessibility, and component architecture work.

- **Repository**: [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
- **Skill Location**: `.agents/skills/ui-ux-pro-max/` (with additional skills for `banner-design`, `brand`, `design`, `design-system`, `slides`, `ui-styling`)

## How to Query Design Intelligence
When designing, reviewing, or fixing UI/UX, run the local search script:
```bash
# Design system recommendation for a project/niche
python .agents/skills/ui-ux-pro-max/scripts/search.py "<niche/product>" --design-system

# Specific UI styles (e.g., bento, glassmorphism, neo-brutalism)
python .agents/skills/ui-ux-pro-max/scripts/search.py "<style name or query>" --domain style

# Color palettes and reasoning
python .agents/skills/ui-ux-pro-max/scripts/search.py "<domain/industry>" --domain color

# Font pairings and typography
python .agents/skills/ui-ux-pro-max/scripts/search.py "<vibe/product>" --domain typography

# UX guidelines & accessibility
python .agents/skills/ui-ux-pro-max/scripts/search.py "<interaction or pattern>" --domain ux

# Stack-specific best practices
python .agents/skills/ui-ux-pro-max/scripts/search.py "<query>" --stack <stack-name>
```

Always aim for high aesthetic quality, clean typography, harmonious palettes, and WCAG accessibility.
