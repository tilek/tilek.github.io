# Site checks

The Deploy site workflow installs the shared build toolchain, builds Jekyll,
prunes unused CSS, and checks the generated site before publishing that same
`_site` directory. A failed build or validation stops publication, keeping the
previous deployed site. Normal GitHub text edits still publish automatically.

Run the checks locally after a production Jekyll build:

```sh
npm ci
npx --no-install purgecss -c purgecss.config.js
python3 -m unittest discover -s tests
python3 scripts/validate_site.py _site
```

Ruby gems are locked in Gemfile.lock and Node tools in package-lock.json.
ImageMagick (`convert`) and Jupyter are installed explicitly by the shared setup
action. Keep dependency updates deliberate and test them on a pull request.

The separate Link report is advisory: external servers can rate-limit or reject
crawlers. Results remain in its job summary and report artifact. YouTube embed
endpoints are excluded from HTTP probing because a direct crawler request can
fail while the embedded player works. Internal pages and image references are
not exempt: the pre-publication validator checks the generated output.

Docker publishing belongs to the upstream template repository. Its existing
owner guard is preserved and correctly applied to the dependent Slim job; this
site's Pages deployment does not need Docker registry credentials.
