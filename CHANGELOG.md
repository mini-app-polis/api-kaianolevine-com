# [2.37.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.36.0...v2.37.0) (2026-10-25)


### Features

* **secrets:** POST /v1/secrets/rotations asks rotator-cog to rotate one secret, admin only ([06cc5ed](https://github.com/mini-app-polis/api-kaianolevine-com/commit/06cc5edfd5cca1ec9f698167fdb40564f5a30da4))

# [2.36.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.35.0...v2.36.0) (2026-10-25)


### Features

* **identity:** accept a previous key per machine, record the matched slot, and dual-accept the GitHub webhook secret ([20489c5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/20489c5c8f9356a5dafc488eb91e5615a8997618))

# [2.35.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.34.0...v2.35.0) (2026-10-25)


### Features

* **secrets:** record secret rotation events and declare the rotator-cog machine ([af324b4](https://github.com/mini-app-polis/api-kaianolevine-com/commit/af324b4ee1695ac9ec95620859915962fce66f50))

# [2.34.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.33.0...v2.34.0) (2026-10-25)


### Features

* **signals:** ask task-manager-cog to comment on a recovered streak's task and move it to Review ([3fa5b7a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3fa5b7a1a72b82d04f6bec8c373210f1761e18a9))

# [2.33.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.32.0...v2.33.0) (2026-10-25)


### Features

* **tasks:** POST /v1/tasks/sweeps enqueues an on-demand board sweep ([79190a7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/79190a793b591887d66daf9828bd8447142d2ce8))

# [2.32.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.31.2...v2.32.0) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.23.0 ([1b56ee5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/1b56ee542516c23d0186c8d14899277c9ceae1a9))


### Features

* **tasks:** grooming claims, so a job delivered twice is groomed once ([6d2bf31](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6d2bf3131da712a7e39c5b38feba769c35a7c728))

## [2.31.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.31.1...v2.31.2) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.22.3 ([910d7e9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/910d7e9088362190686d00f91c8a452f65077a45))

## [2.31.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.31.0...v2.31.1) (2026-10-25)


### Bug Fixes

* **deps:** bump the minor-and-patch group with 10 updates ([a6d5950](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a6d5950b3037c3ccbb321e82b417a62024283a3d))

# [2.31.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.30.5...v2.31.0) (2026-10-24)


### Features

* **notifications:** add a per-environment Discord event allow-list and drop data-changed messages in development ([a29a0d8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a29a0d8de9c61ca4f7d07a10ad8c45af1cebbc6d))

## [2.30.5](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.30.4...v2.30.5) (2026-10-24)


### Bug Fixes

* **signals:** keep repeated Dependabot update runs in one CI streak ([a87c390](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a87c39009ad5e97eb7f6bb57bedcb0c0308e0b06))

## [2.30.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.30.3...v2.30.4) (2026-10-24)


### Bug Fixes

* **wcs-qa:** lessons are visible by source, not by owner ([cf57712](https://github.com/mini-app-polis/api-kaianolevine-com/commit/cf5771283a19e6b35c435b685ba7f2af347b675a))

## [2.30.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.30.2...v2.30.3) (2026-10-24)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.22.1 ([a90b7b4](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a90b7b47e2a0b5018f50f4dcf26b3e0fd54dbdc6))
* **deps:** bump miniapppolis-common-utils to 6.22.2 ([7e3d7e2](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7e3d7e28447826e4b23c91bd75f427bf2b685c13))
* **dispatch:** build queue messages from the shared envelope models ([76c2ab6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/76c2ab69145c2ee08db124b67559b03a90543804))

## [2.30.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.30.1...v2.30.2) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.22.0 ([9c78427](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9c784270fe4bc3cea0299fc6e030531bec6fcde1))
* **deps:** patch vulnerable dependencies (filelock) ([c86b0b0](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c86b0b022db5261e6c9838e6c05ea34830d6aa52))

## [2.30.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.30.0...v2.30.1) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.20.0 ([d12ed17](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d12ed17b4e70bb9e6d832d634c13ee9db8d2c4d0))
* **deps:** require miniapppolis-common-utils 6.21.0 for 25-child grooming proposals ([56c2393](https://github.com/mini-app-polis/api-kaianolevine-com/commit/56c239348c69fd777dd8a8d7f64c7bda2ad603b2))

# [2.30.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.29.0...v2.30.0) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.18.0 ([7cb051b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7cb051b2fa488ef3d13727ba5bc8268b2b2662f0))
* **deps:** bump miniapppolis-common-utils to 6.19.0 ([c7446a3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c7446a34e4544e29168e7f62f3fe64b1ab9a72f9))


### Features

* **tasks:** record agent runs that need grooming, with their questions ([bed5baf](https://github.com/mini-app-polis/api-kaianolevine-com/commit/bed5baf4532357cfe018381238063e163ea856e8))

# [2.29.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.28.1...v2.29.0) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.16.2 ([daa8854](https://github.com/mini-app-polis/api-kaianolevine-com/commit/daa88544d9023a162e2867222b56930becff8889))
* **deps:** bump miniapppolis-common-utils to 6.17.0 ([95d2a5a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/95d2a5a15b7833977e7c0a97fc680de646a10d1e))


### Features

* **tasks:** record agent runs, their spend and results ([cf07139](https://github.com/mini-app-polis/api-kaianolevine-com/commit/cf07139f4f70338e0a307ba8bd30db8949a5eb50))

## [2.28.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.28.0...v2.28.1) (2026-10-22)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.16.1 ([f3a642f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f3a642f67b81971d1f57697dd5013f92b8c76ab4))

# [2.28.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.27.3...v2.28.0) (2026-10-22)


### Features

* **signals:** escalate tasks that arrive ready ([311c19e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/311c19ef30a24ff887152ebda16a405cc1ed97ee))

## [2.27.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.27.2...v2.27.3) (2026-10-22)


### Bug Fixes

* **identity:** stop warning about development-only machines' keys in production ([4ffe960](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4ffe9604cd46669fead73d86aec669d444168c1e))

## [2.27.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.27.1...v2.27.2) (2026-10-22)


### Bug Fixes

* **config:** read the Google service account from GOOGLE_CREDENTIALS_JSON ([cf0f58f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/cf0f58f5c19ca56172df66f5add340d64b84476f))

## [2.27.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.27.0...v2.27.1) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.15.1 ([30b870d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/30b870decca86951dd7a234dd23cb7a95b330f95))

# [2.27.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.26.0...v2.27.0) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.12.0 ([cfbae36](https://github.com/mini-app-polis/api-kaianolevine-com/commit/cfbae367be3d89d2898ff440b6368546f35d5873))


### Features

* **tasks:** hand grooming jobs to task-groomer-cog and its proposals back to task-manager-cog ([0ea7dad](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0ea7dad74ab38bc73cc5bdfe2e506b9a4aee0d1c))
* **tasks:** hand grooming jobs to task-groomer-cog and its proposals back to task-manager-cog ([93e48e2](https://github.com/mini-app-polis/api-kaianolevine-com/commit/93e48e2a160c5fa5dae7d3f688d1e8720f6d2c9c))
* **tasks:** keep the task board's grooming records and the month's paid grooming ([6fc1540](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6fc15401e6ff50d9da029b363a323f45e0b2da0d))
* **tasks:** keep the task board's grooming records and the month's paid grooming ([52270fb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/52270fbee34894c54fda75b3a46776d5733e462e))
* **tasks:** remember what was last sent for grooming ([ee694e3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ee694e35dac4175a97553e892b3876fe549dae70))
* **tasks:** remember what was last sent for grooming ([46eef5e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/46eef5ea86ed7c6e69482c7d69c1ab7e1d449d08))

# [2.26.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.25.0...v2.26.0) (2026-10-21)


### Features

* **signals:** repeated 5xx from one route become a task ([7ab0b30](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7ab0b30a0695db3e18af6cf403e3d6a96a817521))

# [2.25.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.24.1...v2.25.0) (2026-10-21)


### Features

* **signals:** consecutive failing cog runs become a task ([5603581](https://github.com/mini-app-polis/api-kaianolevine-com/commit/56035817c40a756b9cb66bf8884b7905c382bdb3))

## [2.24.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.24.0...v2.24.1) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.11.2 ([345dbfd](https://github.com/mini-app-polis/api-kaianolevine-com/commit/345dbfde88fcd43d50f449e0f3be8faf74806058))

# [2.24.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.23.0...v2.24.0) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.11.1 ([b0cf3b9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b0cf3b94d26a4cca96dd46e8b7a3a738fbe318e2))


### Features

* **signals:** a failed dependency audit dispatches the dependency fix instead of a re-run ([dc30d1f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/dc30d1fd7f7f3c2767d45c80b60de49fede56f92))

# [2.23.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.22.0...v2.23.0) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.11.0 ([a6a5985](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a6a59859c737cbc7bc569af0712c6eaec82046b7))
* **deps:** bump virtualenv for CVE-2026-113725 and related advisories ([a603d8e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a603d8e9b044ae7bbb81e89bbd844c8a0319bc1d))


### Features

* **signals:** failed CI runs become tasks once a re-run does not fix them ([df6ab71](https://github.com/mini-app-polis/api-kaianolevine-com/commit/df6ab7175d9b5c9581b20ba336ceca74f0891e33))

# [2.22.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.21.3...v2.22.0) (2026-10-21)


### Features

* **identity:** transcription-cog may request tasks ([8373b1c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8373b1c4214893763d1fe95dfac5caaabe7a3f4d))

## [2.21.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.21.2...v2.21.3) (2026-10-20)


### Bug Fixes

* **auth:** answer 503 when identity reports the issuer unavailable ([bc65d22](https://github.com/mini-app-polis/api-kaianolevine-com/commit/bc65d2216c306438adaa66717b5760ed26b1fdc8))

## [2.21.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.21.1...v2.21.2) (2026-10-20)


### Bug Fixes

* **railway:** declare every Settings field the service reads so apply cannot remove it ([ecbe11a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ecbe11adac3916c2b11d9f2df12ca91961acfd11))
* **wcs:** log wiki page counts with each embeddings refresh ([baf0462](https://github.com/mini-app-polis/api-kaianolevine-com/commit/baf04624739c2ead85bb69a50b9dba030f9b281c))

## [2.21.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.21.0...v2.21.1) (2026-10-20)


### Bug Fixes

* **auth:** decide WCS visibility by role, fail CORS closed, close contact open redirect ([dc7df25](https://github.com/mini-app-polis/api-kaianolevine-com/commit/dc7df2585524960a50ffa94043bb63c3ddfbfb44))
* **auth:** decide WCS visibility by role, fail CORS closed, close contact open redirect ([0ca5252](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0ca52525693ea5c590910221e223b2bb1bfb7dae))
* **deps:** bump miniapppolis-identity to 2.1.5 ([feed04f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/feed04f422ad70d4b166fc330d0b4e629a31e681))

# [2.21.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.20.0...v2.21.0) (2026-10-20)


### Features

* **tasks:** POST /v1/tasks hands task requests to task-manager-cog ([8bdd583](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8bdd583dbf08928097b97f4b6816c239b9ee4705))

# [2.20.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.19.1...v2.20.0) (2026-10-20)


### Features

* **wcs:** Q&A answers from the wiki pages, and embeddings refresh themselves (MINI-113, MINI-114) ([48c7f5a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/48c7f5a6b52eef64f3217e8a2c716cdc4c8ce1ed))

## [2.19.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.19.0...v2.19.1) (2026-10-19)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.9.0 ([63b90c8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/63b90c83fe405aca66a84abc55fa8de2955a2c73))
* **wcs:** give the API service the wiki-curator-deploy key (MINI-109) ([5df2539](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5df25395e4b3422c042881b78043ec80203b9c6e))

# [2.19.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.18.0...v2.19.0) (2026-10-19)


### Features

* **wcs:** a wiki-operator role and the wiki-curator-deploy machine for rebuilds after a deploy (MINI-109) ([4e54c40](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4e54c40d123588af13e3ead6245935b04673ad55))

# [2.18.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.17.0...v2.18.0) (2026-10-19)


### Bug Fixes

* **deps:** require miniapppolis-common-utils 6.9.0 for the wiki review contract (MINI-101) ([6490cc8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6490cc8a9eac592e87ff9771a56b3156ece6a0e6))


### Features

* **wcs:** correction session type, wiki page revisions and thin list, notes from the site (MINI-102, MINI-101, MINI-98) ([3e2c4e4](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3e2c4e41794a53c83ae082b181b03810e46e0e9c))

# [2.17.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.16.1...v2.17.0) (2026-10-18)


### Features

* **wcs:** a wiki pages write says which pages are new, changed or gone ([2e06a0c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/2e06a0cfc12ad1b8db455d8d98de1e3db2b5df8e))

## [2.16.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.16.0...v2.16.1) (2026-10-18)


### Bug Fixes

* **wcs:** a wiki operation the queue deduplicated says so, with the first send's time (MINI-103) ([01e46e5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/01e46e54822ed1fcc11aea15066ac4904fac3dd0))

# [2.16.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.15.0...v2.16.0) (2026-10-18)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.7.0 ([8e15852](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8e15852f839209ca0f44b7e825eff338669d065b))
* **deps:** common-utils 6.7.1, so a Discord 429 holds sends for at most a minute ([195f6f8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/195f6f81ceb37592c73094b5670ec3ab97495b5a))


### Features

* **wcs:** the wiki export is built once per corpus revision, with ETag, 304 and a 503 when busy (MINI-106) ([2307118](https://github.com/mini-app-polis/api-kaianolevine-com/commit/23071184bef9bc03eb4dc7c69c157dafa82b0423))

# [2.15.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.14.0...v2.15.0) (2026-10-18)


### Features

* **wcs:** wiki jobs run side by side where they write different state (MINI-105) ([4f661b6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4f661b6c115035acac2d1e2a87fc1144dde8a53c))

# [2.14.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.13.0...v2.14.0) (2026-10-18)


### Features

* **wcs:** the admin starts free wiki operations from /admin/wiki (MINI-103) ([1310a5e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/1310a5e52355606140025a7d2b534ebba3f485ed))

# [2.13.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.12.0...v2.13.0) (2026-10-18)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.6.0 ([5402d04](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5402d0492549820397df1a51dc4f8929610a6231))


### Features

* **wcs:** a folded slug is served the page it folded into (MINI-100) ([f590208](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f59020868fac3772e807a8ff99519945958b7c67))

# [2.12.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.11.1...v2.12.0) (2026-10-17)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.5.0 ([47c7e6e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/47c7e6ee1a111800798bf109f1302bee6260c349))


### Features

* **wcs:** wiki page documents — stored, filtered by source visibility, served (MINI-96) ([082e44f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/082e44fb1233fe056218cb6ee9971053c45594e7))

## [2.11.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.11.0...v2.11.1) (2026-10-17)


### Bug Fixes

* **wcs-wiki:** declare literal scopes on wiki state routes and document state service ([f1e601c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f1e601c50a3d35696f22ea1326ce9cda538b801b))

# [2.11.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.10.0...v2.11.0) (2026-10-17)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.4.0 ([c864e96](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c864e9615112083e5fa49683312409f3493a5f55))


### Features

* **wcs:** wiki jobs — dispatch to wiki-curator, behind a flag (MINI-95) ([9251b06](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9251b064b6dcc7481287906f42ea8f2d3dcdfd0a))

# [2.10.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.9...v2.10.0) (2026-10-17)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.3.0 ([d97193f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d97193f52edb6acc556b6c4de7033e32c15ddb5d))


### Features

* **wcs:** wiki curator state tables and endpoints (MINI-89) ([499c4be](https://github.com/mini-app-polis/api-kaianolevine-com/commit/499c4be38f8343485fec1c91c9766fa85e8e451c))

## [2.9.9](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.8...v2.9.9) (2026-10-17)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.2.0 ([90764e1](https://github.com/mini-app-polis/api-kaianolevine-com/commit/90764e164e9d37807ac24a2da1249aa951c6fb0e))

## [2.9.8](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.7...v2.9.8) (2026-10-16)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.1.0 ([754307a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/754307aa66ce068869698745d0d0a5f832a4963a))
* **deps:** bump miniapppolis-common-utils to 6.1.1 ([ef8a1ad](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ef8a1adf83ad473ce4bf1a79d98cc9e44f59fa16))

## [2.9.7](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.6...v2.9.7) (2026-10-16)


### Bug Fixes

* **railway:** pass DB_OPS_API_KEY to the API service ([df203f3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/df203f36d29886ba944f94080480eb6dc0fb3619))
* **railway:** pass DB_OPS_API_KEY to the API service ([b54278b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b54278bad9c824fd7ec801d440c42901954439eb))

## [2.9.6](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.5...v2.9.6) (2026-10-16)


### Bug Fixes

* **deps:** require miniapppolis-common-utils 6 (>=6.0.1) ([c6c44eb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c6c44ebbcb65fbf0894d3dcb8e3b836252c5c3f2))

## [2.9.5](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.4...v2.9.5) (2026-10-16)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.5 ([f951686](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f951686f571a669e230fdac3c84e2dd047450a62))

## [2.9.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.3...v2.9.4) (2026-10-15)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.4 ([945e0ab](https://github.com/mini-app-polis/api-kaianolevine-com/commit/945e0ab6a761095d40398e33c820da26f5ca8b8c))

## [2.9.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.2...v2.9.3) (2026-10-15)


### Bug Fixes

* **ingest): clean genre spellings on ingest, fix(data:** one spelling per genre ([830a5e9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/830a5e937f644a31ee4af0e6b84f84e0b3bd3d24))
* **stats): never count the silence placeholder, fix(data:** stop counting the silence plays already ingested ([aeea0d3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/aeea0d38daf38afecfbb8b1a2aebf3831597a25c))
* **stats:** never count the silence placeholder ([7b22706](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7b2270603270911236587cc249956b9fe0e91f0e))

## [2.9.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.1...v2.9.2) (2026-10-15)


### Bug Fixes

* **data:** merge eight songs held under two spellings ([3d3f87c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3d3f87c8f5302c7dae8a21b66f8b3b77cc74ebb5))

## [2.9.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.9.0...v2.9.1) (2026-10-15)


### Bug Fixes

* **data:** merge five songs held under two spellings ([3021f39](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3021f393d932f44b591a8e61daea4fdb6e745972))
* **ingest): read an underscore artist list as commas, fix(data:** merge songs split by underscore artists and two versions ([2e4555d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/2e4555d0ca90568237dec2a44dec6be1f93da423))

# [2.9.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.8.2...v2.9.0) (2026-10-15)


### Bug Fixes

* **ingest): file a repeated row under the song it was filed under before, fix(catalog:** give the catalog list a stable order ([9a961f3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9a961f334c2fd20e24c6960db77f04f4f1854f70))
* **ingest:** ignore file tags in the Remix column ([4cd685e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4cd685e978b3c14d9f57c17ab933d03185a6894c))


### Features

* **notify:** copy failing run reports to the errors channel ([47a74ec](https://github.com/mini-app-polis/api-kaianolevine-com/commit/47a74ec2956d4f778a1f9531d4cf19c305e756c3))

## [2.8.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.8.1...v2.8.2) (2026-10-15)


### Bug Fixes

* **ingest): file a play under its version from the Remix column and title, fix(data:** key version catalog entries by their full title ([533f43a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/533f43a735f4d0cbeabb37387a5d0c640c84534f))

## [2.8.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.8.0...v2.8.1) (2026-10-15)


### Bug Fixes

* **data:** file the last 43 misfiled version plays ([ab655bd](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ab655bdaad1d7d15a121d98149e467d3294d1093))

# [2.8.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.8...v2.8.0) (2026-10-15)


### Features

* **identity:** declare the db-ops machine ([eeac87d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/eeac87d17e6ff0930ba17bbabb24b51ea5458c21))

## [2.7.8](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.7...v2.7.8) (2026-10-15)


### Bug Fixes

* **data:** split plain plays out of six version entries ([c0f82f6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c0f82f640956582c9f8d6789e47a2c77457bbaf5))

## [2.7.7](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.6...v2.7.7) (2026-10-15)


### Bug Fixes

* **data:** file version plays from 2022 on under their version ([d70fc0e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d70fc0ed7bb76cc4bbf5e78c81ed1ab4942003b8))

## [2.7.6](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.5...v2.7.6) (2026-10-15)


### Bug Fixes

* **data): restore the 2023-02-04 Charlotte Westie Fest set, fix(api:** return label, remix and comment on track responses ([767cd13](https://github.com/mini-app-polis/api-kaianolevine-com/commit/767cd13cc863eb4bbd49d2d70765cca05113508b))
* **data:** correct the 2022-11-19 DCSX Saturday song lengths ([8cf7309](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8cf730978d7d5a9b8254b1c963f254d5280e9ba4))
* **data:** count Coke Henny Pt 2 as Coke & Henny Pt. 2 ([0abe98f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0abe98f38c467dbc457a49c92bdd89e171bbdf56))
* **data:** restore two 2023 plays dropped for a blank artist ([4f0e098](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4f0e098f275392267cc0857afeabbfc7914a1613))

## [2.7.5](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.4...v2.7.5) (2026-10-15)


### Bug Fixes

* **data:** count DJ Don't (Remix) as DJ Don't ([d37dd08](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d37dd08437ab0115bd2ae48c9e00b30b02e6f71e))
* **data:** honour the Remix column on plays from 2022 on ([65e6bdf](https://github.com/mini-app-polis/api-kaianolevine-com/commit/65e6bdfe1f15e4b346024d6c4fc20b7854fc00f4))

## [2.7.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.3...v2.7.4) (2026-10-15)


### Bug Fixes

* **data:** merge catalog entries that are the same recording ([a7df636](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a7df63661abc1b770da026c95340b17198f64d8d))

## [2.7.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.2...v2.7.3) (2026-10-15)


### Bug Fixes

* **data:** generate track ids in the blank-artist restore ([c03429a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c03429ad52be4ccf5120946d1fef93bc0f264712))

## [2.7.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.1...v2.7.2) (2026-10-15)


### Bug Fixes

* **data:** restore nine plays dropped for a blank artist ([6d94aa3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6d94aa3d8805e3534472211b020ee1a86deea1e1))

## [2.7.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.7.0...v2.7.1) (2026-10-15)


### Bug Fixes

* **stats:** count one song once across catalog owners ([5ebe5cf](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5ebe5cf28ed3e4db49ca04053f8fd700d21eb0b1))

# [2.7.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.6.4...v2.7.0) (2026-10-13)


### Features

* **cogs:** make the deejay and transcription queues FIFO ([900f2e3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/900f2e3b239db9bf4fab7b81152d86aaa241721a))

## [2.6.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.6.3...v2.6.4) (2026-10-13)


### Bug Fixes

* **github-status:** warm the snapshot at boot so deploys never land on a visitor ([beff4b3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/beff4b37d76f92a09d1b3cf3d5955efad073be67))

## [2.6.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.6.2...v2.6.3) (2026-10-12)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.3 ([9ca70f2](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9ca70f26c25dd2f7e52f2eb85b9851fa2ad364e0))

## [2.6.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.6.1...v2.6.2) (2026-10-12)


### Bug Fixes

* **deps:** bump the minor-and-patch group with 4 updates ([851b3a6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/851b3a6b2d8b8cf9d4ba13ee2fb77cf440499e6a))

## [2.6.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.6.0...v2.6.1) (2026-10-11)


### Bug Fixes

* **deps:** bump miniapppolis-identity to 2.1.4 ([145c58c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/145c58c451abf23a44c57777ebff17cc8bae61cf))

# [2.6.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.5.0...v2.6.0) (2026-10-11)


### Bug Fixes

* **stats:** derive year summaries only from the database ([05a98e8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/05a98e85d4f0827a3a16fff2aa8ed40455345622))
* **stats:** type the collection query for mypy ([fca07b3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/fca07b3312609f91bf4f0dae4177171df5e62e37))


### Features

* **stats:** complete collection endpoint ([ca22158](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ca22158e2cafe8bdc586718b8a641cc9bb2331a5))

# [2.5.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.4.1...v2.5.0) (2026-10-11)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.1 ([6fee416](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6fee4163b4425f2f63b2e90d6a8bd8271ce5e32e))
* **deps:** bump miniapppolis-common-utils to 5.25.2 ([5028478](https://github.com/mini-app-polis/api-kaianolevine-com/commit/50284788351965afecda261583c79da7eb30ed79))


### Features

* **stats:** annual DJ summaries with curated layer ([a4e643b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a4e643b3150709102353f30ba66c9a0464374989))

# [2.5.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.4.1...v2.5.0) (2026-10-11)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.1 ([6fee416](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6fee4163b4425f2f63b2e90d6a8bd8271ce5e32e))


### Features

* **stats:** annual DJ summaries with curated layer ([a4e643b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a4e643b3150709102353f30ba66c9a0464374989))

## [2.4.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.4.0...v2.4.1) (2026-10-10)


### Bug Fixes

* **spotify:** declare response_model=None on the re-auth routes ([18ac962](https://github.com/mini-app-polis/api-kaianolevine-com/commit/18ac962dfcf3ccc2bff5d2c0724ef24562ed4e54))

# [2.4.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.18...v2.4.0) (2026-10-09)


### Bug Fixes

* **spotify:** show fixed text on re-auth failure pages, never exception detail ([7457829](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7457829ac295e7f62d5853a595e26c1aa41f3e1e))


### Features

* **spotify:** one-click re-auth and expiry reminders for deejay-cog's token ([dc74d2f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/dc74d2f068f5cee1c040acfc7fef81e894ab563f))

## [2.3.18](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.17...v2.3.18) (2026-10-09)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.0 ([836ad98](https://github.com/mini-app-polis/api-kaianolevine-com/commit/836ad98c600eaea81b83f594a603f7f78a11c52d))

## [2.3.17](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.16...v2.3.17) (2026-10-09)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.24.1 ([47cdba9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/47cdba9db1fad3e1fc238de7901e17735376cf08))

## [2.3.16](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.15...v2.3.16) (2026-10-09)


### Bug Fixes

* **notifications:** keep GitHub deliveries inside GitHub's timeout on Discord rate limits ([0770e16](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0770e1649f60058e556d7bf0e0221dc2e5a1bbc6))

## [2.3.15](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.14...v2.3.15) (2026-10-09)


### Bug Fixes

* **notifications:** keep evaluation findings out of the change feed ([e8b42c6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e8b42c6e7cfde6a5f71da8012400b694699494d7))

## [2.3.14](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.13...v2.3.14) (2026-10-09)


### Bug Fixes

* **notifications:** report a dropped dispatch once, labelled, without exception text ([18a58a2](https://github.com/mini-app-polis/api-kaianolevine-com/commit/18a58a2fc578c1faa1a1c32e62efccbfe7e86616))

## [2.3.13](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.12...v2.3.13) (2026-10-08)


### Bug Fixes

* **observability:** read Sentry DSN from SENTRY_DSN_APIS and tag service ([66dc9d8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/66dc9d88487d20f72a8ec6e883043d90584a7b6a))

## [2.3.12](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.11...v2.3.12) (2026-10-08)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([29d60a0](https://github.com/mini-app-polis/api-kaianolevine-com/commit/29d60a0484d46fe92b2e7956e838e526f751e1fa))

## [2.3.11](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.10...v2.3.11) (2026-10-08)


### Bug Fixes

* **deps:** bump the minor-and-patch group with 4 updates ([754030d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/754030db6b53c585398ebcf62d92518a05ccebd5))

## [2.3.10](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.9...v2.3.10) (2026-10-07)


### Bug Fixes

* **wcs:** apply metadata corrections, 404 unknown sources, follow name corrections on instructor pages ([820decb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/820decb13d31fb0da4bed22f9fde8ef05a02d931))

## [2.3.9](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.8...v2.3.9) (2026-10-07)


### Bug Fixes

* **deps:** bump the minor-and-patch group with 3 updates ([2b6e86c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/2b6e86cf048bece52c776cd3d690f35e03a5fa0a))

## [2.3.8](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.7...v2.3.8) (2026-10-07)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([454d100](https://github.com/mini-app-polis/api-kaianolevine-com/commit/454d100972fc3b5bf58caabe7e3d5be386a664a5))

## [2.3.7](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.6...v2.3.7) (2026-10-06)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([bb8cf7b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/bb8cf7b6f6b84ae9ddd12a7f9e295e54c074aaf2))
* **deps:** bump the minor-and-patch group with 3 updates ([49a352c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/49a352c5157be0dfa9dc2acd16f0eef68bd711f4))

## [2.3.6](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.5...v2.3.6) (2026-10-05)


### Bug Fixes

* **deps:** bump boto3 ([8332be7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8332be731fbf07796f1d5dbe5919c539ccc439c6))
* **deps:** bump miniapppolis-common-utils ([180e69d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/180e69daaa238b2ec552dae25c22274c5d764c03))

## [2.3.5](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.4...v2.3.5) (2026-09-30)


### Bug Fixes

* **github-status:** serve the expired snapshot and refresh behind it ([f80ec04](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f80ec044c0862b59026801e3d7366dd567480fe3))

## [2.3.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.3...v2.3.4) (2026-09-30)


### Bug Fixes

* **deps:** common-utils 5.17.0 for slow-request logging ([5c51a42](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5c51a42ad2d6742c224003b81c699df39cfb0160))
* **deps:** common-utils 5.17.0 for slow-request logging ([f4e1ec6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f4e1ec613e4c69241c01b548386c78d56e643337))

## [2.3.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.2...v2.3.3) (2026-09-30)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([4696d34](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4696d34051e45314277d0594d140c139efeea43a))

## [2.3.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.1...v2.3.2) (2026-09-30)


### Bug Fixes

* **deps:** bump urllib3 to 2.8.0 for CVE-2026-97687, CVE-2026-97688 and CVE-2026-97689 ([b134499](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b134499136a5b9ecee0750912e0b6d29b350757d))
* **deps:** bump virtualenv to 21.14.1 for PYSEC-2026-4011, PYSEC-2026-4012, PYSEC-2026-4013 and PYSEC-2026-4014 ([2a7c4ef](https://github.com/mini-app-polis/api-kaianolevine-com/commit/2a7c4ef66f00944df2f4c7a784fa113187c5178c))

## [2.3.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.3.0...v2.3.1) (2026-09-30)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils in the commons group ([aceeb12](https://github.com/mini-app-polis/api-kaianolevine-com/commit/aceeb12f66358b4b635ad973ed3a985df1156c20))

# [2.3.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.2.0...v2.3.0) (2026-09-29)


### Features

* **metrics:** per-route latency for the standards catalog and evaluation writes ([037a163](https://github.com/mini-app-polis/api-kaianolevine-com/commit/037a163748a9a8095976904142a60e5bcb082758))

# [2.2.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.1.2...v2.2.0) (2026-09-29)


### Bug Fixes

* **deps:** bump oauthlib to 4.0.0 and pyjwt to 2.15.1 for CVE-2026-49265 and CVE-2026-102274 ([9c4d894](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9c4d894d3d07b27029a7fa79f9b76c0ceef94423))


### Features

* **metrics:** publish request latency and error counts to CloudWatch ([ed8487e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ed8487e84c175d8ce06573b34041308b5df41642))

## [2.1.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.1.1...v2.1.2) (2026-09-28)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils in the commons group ([8a2aa9e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8a2aa9e0dbf3dc26ca77b5c3399e6489248bebef))

## [2.1.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.1.0...v2.1.1) (2026-09-28)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 4 updates ([8bfebe6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8bfebe6f12d0141920365c7a9a6ad54bea3e6fdf))

# [2.1.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.0.5...v2.1.0) (2026-09-26)


### Features

* **dispatch:** claim Drive files so repeated asks become one job ([56bc05f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/56bc05f49011d91f3faab596a5a535b4e8bc18c1))

## [2.0.5](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.0.4...v2.0.5) (2026-09-24)


### Bug Fixes

* **deps:** require common-utils 5.12.1 for webhook token redaction ([7b09198](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7b091984c289492ae3c635358429ce044130748b))
* **deps:** require common-utils 5.13.1 ([60fd7e0](https://github.com/mini-app-polis/api-kaianolevine-com/commit/60fd7e0bb45cce7733ab396d40ba72c022603ec9))

## [2.0.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.0.3...v2.0.4) (2026-09-23)


### Bug Fixes

* **discord:** hold sends through a 429 instead of posting through it ([314686b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/314686b451eafc68ca690ca719fa1f72f5f4c00e))

## [2.0.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.0.2...v2.0.3) (2026-09-23)


### Bug Fixes

* **discord:** hold sends through a 429 and redact webhook tokens in httpx logs ([fdc13ea](https://github.com/mini-app-polis/api-kaianolevine-com/commit/fdc13ea2148ac77a571fbc418de2db38331cbbef))

## [2.0.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.0.1...v2.0.2) (2026-09-23)


### Bug Fixes

* **contact:** report a missing Turnstile secret as a config error ([f15fae8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f15fae8f18b6dd588664ad11e30c995454f7bf6d))

## [2.0.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v2.0.0...v2.0.1) (2026-09-23)


### Bug Fixes

* **config:** drop env keys for settings that no longer exist ([9a37b8c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9a37b8c6f02cec2c13e6aef67d1bde71384f07c6))

# [2.0.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.68.0...v2.0.0) (2026-09-23)


* refactor!: remove the Prefect flow-state webhook ([d88e92f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d88e92f858e5fa981893cccfc96cca820f28152f))


### BREAKING CHANGES

* POST /v1/prefect-webhook is gone, and
PREFECT_WEBHOOK_SECRET and PREFECT_NOTIFY_STATES are no longer read.

# [1.68.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.67.0...v1.68.0) (2026-09-22)


### Features

* adding transcription endpoint for aws trigger ([976f7d7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/976f7d7b977f4044115829e3a9ed9607b9c26a15))

# [1.67.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.66.1...v1.67.0) (2026-09-21)


### Bug Fixes

* **dispatch:** derive each cog's queue from its name and the environment ([ceba53b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ceba53b9d3303e8ebe4eb8664d1d8cb987b32f71))


### Features

* **deejay:** enqueue deejay-cog runs via POST /v1/deejay/runs ([779320e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/779320e4123b34b73ebe44d436d1177faa76a4ba))

## [1.66.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.66.0...v1.66.1) (2026-09-21)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 4 updates ([241b981](https://github.com/mini-app-polis/api-kaianolevine-com/commit/241b981ec1c44c57c7ef67094172127404db5ceb))

# [1.66.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.65.1...v1.66.0) (2026-09-19)


### Features

* **evaluations:** record the evaluator version on each finding ([d79f256](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d79f25644efa385ef54f10c294c3832095b24d66))

## [1.65.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.65.0...v1.65.1) (2026-09-17)


### Bug Fixes

* Fixing error message found in production ([5932f34](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5932f342507c295f209026709e7bc1af16f01b8b))

# [1.65.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.64.0...v1.65.0) (2026-09-16)


### Features

* **evaluations:** dispatch the fleet-scoped checks with the pass ([48f1d6b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/48f1d6ba95f7e67863ae7f46be6d656e708a54ec))

# [1.64.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.63.0...v1.64.0) (2026-09-16)


### Features

* **evaluations:** add POST /v1/evaluations/introspection ([1911403](https://github.com/mini-app-polis/api-kaianolevine-com/commit/1911403f37d9367466954e158ef8db3598edfdeb))

# [1.63.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.62.1...v1.63.0) (2026-09-16)


### Features

* **evaluate:** route fleet scope to the fan-out endpoint ([7af606f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7af606fbdda7183e40611bbdc554ce5d629ea8f2))
* **evaluations:** fan the fleet out to one job per repository ([59462d7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/59462d72e3c9834b2aef170564fa5df1384c0e53))

## [1.62.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.62.0...v1.62.1) (2026-09-15)


### Bug Fixes

* **evaluations:** name the queue credentials for their caller ([9434aa5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9434aa5be3dfb9484a8709a2f0f8a1772f589436))

# [1.62.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.61.0...v1.62.0) (2026-09-15)


### Features

* **evaluations:** enqueue evaluation jobs on SQS ([739a119](https://github.com/mini-app-polis/api-kaianolevine-com/commit/739a119a76b5204cc0bf04e69289ade77daf2b2f))

# [1.61.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.60.1...v1.61.0) (2026-09-15)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 3 updates ([5bfcae2](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5bfcae2ec3ee2c6209021844fa0c83e36c31fbf5))


### Features

* **evaluations:** server-side idempotency for findings (PIPE-002) ([a49c062](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a49c062de197fd68ac99ead28f5713abde71ae62))

## [1.60.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.60.0...v1.60.1) (2026-09-13)


### Bug Fixes

* pin evaluate.yml to [@v3](https://github.com/v3) ([a594d8d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a594d8d68f7b01b8ff6faf202193ad986805611d))

# [1.60.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.59.0...v1.60.0) (2026-09-13)


### Features

* a shared workflow for asking that a repo be evaluated ([214ddf6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/214ddf6a2916e30a24e359db3c45a3586ed7f714))

# [1.59.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.58.0...v1.59.0) (2026-09-13)


### Features

* accept evaluation requests and hand them to the evaluator ([53b7b3f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/53b7b3fd495982b47e084e910e165a5aac158eb5))
* accept fleet sweep requests ([e7d3445](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e7d34457f1f21048b5fa576bcad7144bcdde5887))

# [1.58.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.57.0...v1.58.0) (2026-09-11)


### Features

* serve the standards catalog as versioned data ([61c0b0e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/61c0b0e8e3fc48fedac11475f32c8eeb3a381f90))

# [1.57.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.56.0...v1.57.0) (2026-09-11)


### Features

* route discord notifications to separate channels ([ef4ca3e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ef4ca3ebe5c35b35571d00c7ffb4b7105ea107fa))

# [1.56.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.55.1...v1.56.0) (2026-09-10)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 2 updates ([e40f263](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e40f263072084dcec46e943664baacf704205e79))
* moving to common 5.7.1 ([304a036](https://github.com/mini-app-polis/api-kaianolevine-com/commit/304a0362c81060bd3c77d44efc417fb93e0666e7))
* test setup ([57dbdc8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/57dbdc837395aa100d3b3eaa3a4a22167bf1b5b4))


### Features

* **environment:** mark non-production Discord messages built by the API ([78afbe9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/78afbe93df0ab87b00f0578868df39262542e7c7))

## [1.55.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.55.0...v1.55.1) (2026-09-09)


### Bug Fixes

* **activity:** report the exception type, never its message ([2088057](https://github.com/mini-app-polis/api-kaianolevine-com/commit/2088057c5bdda9f91e2c60f7e228ca1535c9a46d))

# [1.55.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.54.4...v1.55.0) (2026-09-09)


### Features

* **notifications:** report committed data changes and server faults to discord ([4fdff6f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4fdff6f9c2c8c494ae0524fe446224c929f698f3))

## [1.54.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.54.3...v1.54.4) (2026-09-09)


### Bug Fixes

* checks build history for status ([e1545ed](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e1545edf3a835c4507a3beeb18ef4ee89ef5e9e3))

## [1.54.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.54.2...v1.54.3) (2026-09-09)


### Bug Fixes

* adding private repo metadata ([ac0c0bb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ac0c0bba745562e895a86a176258a85ce39c73cc))

## [1.54.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.54.1...v1.54.2) (2026-09-09)


### Bug Fixes

* adding branch count ([c954190](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c954190a51b5caea5f6e1e1d87b978cecb5e50d7))

## [1.54.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.54.0...v1.54.1) (2026-09-09)


### Bug Fixes

* adding specific token ([2421774](https://github.com/mini-app-polis/api-kaianolevine-com/commit/24217743e01bfd17c6ce9bb21b3c2f626595594a))

# [1.54.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.53.2...v1.54.0) (2026-09-09)


### Bug Fixes

* adding dgp to org list ([116f962](https://github.com/mini-app-polis/api-kaianolevine-com/commit/116f962a034dd0b9f245a8aedeaa9b3646e4b92a))
* **github:** add cached repo status endpoint ([4494151](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4494151fae689e81eddd4c013fca96ddae965fb4))


### Features

* **github:** add cached repo status endpoint ([70e569b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/70e569b16edcf6f7bf978574a29658dc382bf9b5))

## [1.53.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.53.1...v1.53.2) (2026-09-09)


### Bug Fixes

* **auth:** return 503 when the identity provider is unreachable ([d4a7c6e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d4a7c6e1a0b65fdfb14e217e12b1df33f5ba8892))

## [1.53.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.53.0...v1.53.1) (2026-09-09)


### Bug Fixes

* ci guard ([9db0059](https://github.com/mini-app-polis/api-kaianolevine-com/commit/9db0059e03cb37ab1c27f18872b92a1738991231))

# [1.53.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.52.2...v1.53.0) (2026-09-08)


### Features

* finalizing notifications and run findings ([fa818a7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/fa818a78d131aad73d15ade4258750567f550f0b))

## [1.52.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.52.1...v1.52.2) (2026-09-08)


### Bug Fixes

* modifying notification behavior ([d4a5b77](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d4a5b77699e4905370182a1cebfff51dcf115005))

## [1.52.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.52.0...v1.52.1) (2026-09-08)


### Bug Fixes

* **deps:** bump openai from 1.109.1 to 3.8.0 ([af703db](https://github.com/mini-app-polis/api-kaianolevine-com/commit/af703db784b1686aefbbfad25fae7c4da2e5db54))

# [1.52.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.7...v1.52.0) (2026-09-08)


### Features

* adding notification processing from github and custom ([cc02540](https://github.com/mini-app-polis/api-kaianolevine-com/commit/cc0254074eb3136ded9951cd2088a59848a8b1a9))

## [1.51.7](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.6...v1.51.7) (2026-09-07)


### Bug Fixes

* remove uv.lock from release (PY-017) ([0e853b4](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0e853b43abcfd4d6abd4e3a82c8295a4c16240a3))

## [1.51.6](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.5...v1.51.6) (2026-09-07)


### Bug Fixes

* include uv.lock in release artifacts ([bb3eadf](https://github.com/mini-app-polis/api-kaianolevine-com/commit/bb3eadf067700b679977f66b82be59277ab4fdbc))

## [1.51.5](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.4...v1.51.5) (2026-09-06)


### Bug Fixes

* **release:** source the version from a committed file instead of pyproject.toml ([4a6cf3c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4a6cf3c499aaffe72e5a522c252a1710df70b544))

## [1.51.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.3...v1.51.4) (2026-09-05)


### Bug Fixes

* **deps:** consume miniapppolis-common-utils and miniapppolis-identity from PyPI ([f98d769](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f98d769a5a0892afdd118f31108073e6b92fcd89))

## [1.51.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.2...v1.51.3) (2026-09-05)


### Bug Fixes

* **wcs:** mint citation links in the shape the site can actually serve ([a78232d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a78232d5f3f03135a6f7d9f0d3ea2107a49c7511))

## [1.51.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.1...v1.51.2) (2026-09-05)


### Bug Fixes

* **wcs:** take profile identity from the verified token, not the request body ([5f4b907](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5f4b9078352e37c8342debf3c2ee73a6f8368890))

## [1.51.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.51.0...v1.51.1) (2026-09-04)


### Bug Fixes

* **deps:** bump anthropic from 0.125.0 to 1.3.0 ([62d0540](https://github.com/mini-app-polis/api-kaianolevine-com/commit/62d0540bd73545252ffdacb5a473a8774d04c730))
* **deps:** bump openai from 1.109.1 to 3.6.0 ([50a58ef](https://github.com/mini-app-polis/api-kaianolevine-com/commit/50a58efc2884fc6c709dfe73dd38e7082bcef519))
* **deps:** bump pre-commit from 3.8.0 to 4.6.2 ([db6abfe](https://github.com/mini-app-polis/api-kaianolevine-com/commit/db6abfe0a2e263c544d41aba11450fc20c240e3c))
* **deps:** repair the lockfile a Dependabot branch merge corrupted ([7695d97](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7695d979881806dd412751e1c18e227646b50f43)), closes [#7](https://github.com/mini-app-polis/api-kaianolevine-com/issues/7)
* **git:** never three-way merge a lockfile ([a8d205a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a8d205abf4aed2427ecdf5c40847ee9bd8b5c3bc))

# [1.51.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.50.0...v1.51.0) (2026-09-04)


### Features

* **deps:** automate dependency updates ([a2e12e9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a2e12e95ab19b6c50ea4e75abb30928b45bfb9b5))

# [1.50.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.49.1...v1.50.0) (2026-09-04)


### Features

* **ops:** separate the migration role from the request path ([2199104](https://github.com/mini-app-polis/api-kaianolevine-com/commit/219910441a394154509d34d404c0785b65ab6098))

## [1.49.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.49.0...v1.49.1) (2026-09-04)


### Bug Fixes

* **logging,docs:** shared logger in identity_registry, header parity noted ([b623b6a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b623b6aff92a65ea0e6fd709694b0b3822344aa7))

# [1.49.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.48.0...v1.49.0) (2026-09-04)


### Features

* **deploy:** declare resource ceilings (placeholder values) ([63e4dc5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/63e4dc56518402763021f2e2e791b3d001a16540))

# [1.48.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.47.1...v1.48.0) (2026-09-04)


### Features

* **deploy:** declare the restart policy alongside the start command ([09842fb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/09842fbcc13e48f3ba7adecd89c40973c43640d8))

## [1.47.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.47.0...v1.47.1) (2026-09-04)


### Bug Fixes

* **identity:** declare whoami's response shape and document the handler ([08e115b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/08e115b8fc2b14647222f2e19547667772bebb4b))

# [1.47.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.46.4...v1.47.0) (2026-09-03)


### Bug Fixes

* **deps:** widen two caps and refresh the lock to clear 43 advisories ([924ee54](https://github.com/mini-app-polis/api-kaianolevine-com/commit/924ee54eb7ffd3d991a82aab19d83ee8bbc274b3))


### Features

* **security:** call the shared security workflow ([a8adb23](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a8adb23643e757cebdcdc7b159ab235d0786c6ed))

## [1.46.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.46.3...v1.46.4) (2026-09-02)


### Bug Fixes

* **auth:** restore admin-only access to the four gaps endpoints ([703aa8e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/703aa8ed8d478569de4bcf05e4a6d166bd2a9b6c))

## [1.46.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.46.2...v1.46.3) (2026-09-02)


### Bug Fixes

* **auth:** stop granting the unfiltered corpus to every signed-in user ([c5600b6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c5600b669ad7a13c48d686661e402bb87b51fb69))

## [1.46.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.46.1...v1.46.2) (2026-09-02)


### Bug Fixes

* **auth:** enumerate all three authenticated-only routes in the docstring ([3757ccf](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3757ccf47f59bed0a9b727a701d022ac0d4e8675))

## [1.46.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.46.0...v1.46.1) (2026-09-02)


### Bug Fixes

* **auth:** remove the Clerk machine path and its dead guards ([7f9cad0](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7f9cad074b66e60d1124b96f6e1b575acb89fadc))

# [1.46.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.45.2...v1.46.0) (2026-09-02)


### Features

* **identity:** enforce a scope on every authenticated endpoint ([819b3dd](https://github.com/mini-app-polis/api-kaianolevine-com/commit/819b3dd19ea523f9d7494cab20e857c78d63d558))

## [1.45.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.45.1...v1.45.2) (2026-09-02)


### Bug Fixes

* **deps:** pin identity v1.1.1 ([7920cdf](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7920cdf693bacc057adb807d2fdcd84e234d1156))

## [1.45.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.45.0...v1.45.1) (2026-09-02)


### Bug Fixes

* **deps:** relock identity v1.1.0 and common-python-utils v3.2.0 ([d856380](https://github.com/mini-app-polis/api-kaianolevine-com/commit/d856380096a7560b362c7ec47c1f673ee07d975c))

# [1.45.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.44.0...v1.45.0) (2026-09-02)


### Features

* **identity:** declare all five cogs and add the wcs-writer role ([45fa008](https://github.com/mini-app-polis/api-kaianolevine-com/commit/45fa00836d4a286cc847ed13ba7b61c2582d206c))

# [1.44.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.43.1...v1.44.0) (2026-09-02)


### Bug Fixes

* finalizing identity procresses ([ec747d5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ec747d5660a80155410547a551de79c1947643a2))


### Features

* fix api ([954f016](https://github.com/mini-app-polis/api-kaianolevine-com/commit/954f016fb93da0f40ab8d5be6e2c777c244e5f47))

## [1.43.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.43.0...v1.43.1) (2026-09-01)


### Bug Fixes

* filling out auth ([0d0d3da](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0d0d3da24e1293cd7557362f5f231e26db98e526))

# [1.43.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.42.2...v1.43.0) (2026-09-01)


### Features

* **identity:** pin identity v1.0.1, add principal seed script ([c6f9e76](https://github.com/mini-app-polis/api-kaianolevine-com/commit/c6f9e7646d919b59e53a915cc9e9eaee91dcd53b))
* migrating auth ([5449bee](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5449beeeab903fcdb46e687df10db1280cb089eb))

## [1.42.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.42.1...v1.42.2) (2026-06-07)


### Bug Fixes

* adjusting tests for conformnce checker ([4ec3343](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4ec3343e8bd78dd434781c407109cd3dd795e9d4))

## [1.42.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.42.0...v1.42.1) (2026-05-29)


### Bug Fixes

* **composition:** write one extraction row per item; derive co-taught attribution at read time ([b0aff1d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b0aff1d6478a8b45b697fd2379d9ad937dea613c))

# [1.42.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.41.0...v1.42.0) (2026-05-29)


### Features

* **wcs:** include resolved entity/instructor names in source view response ([e1e893a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e1e893a0dc0e95de8f8eda20c54bfcbd9fc2dd6a))

# [1.41.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.40.1...v1.41.0) (2026-05-28)


### Features

* GET /v1/wcs/wiki/export is admin-only, returns full corpus ([58ac1ca](https://github.com/mini-app-polis/api-kaianolevine-com/commit/58ac1ca40cf927507210be45150c1621df299bdd))
* require_wcs_service for machine callers; export accepts admin OR service ([759875b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/759875b9fd557d8ca4f298510eb04cbc823d4110))
* require_wcs_service; export is cog-only, returns full corpus ([91ffa16](https://github.com/mini-app-polis/api-kaianolevine-com/commit/91ffa161281c8c3a01748faf50de94f8877bc97a))

## [1.40.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.40.0...v1.40.1) (2026-05-28)


### Bug Fixes

* expose entity_id on WcsSourceAttributionItem ([297f355](https://github.com/mini-app-polis/api-kaianolevine-com/commit/297f355f2e07e6371a8a5ff5a39d92a3a0ccf47f))

# [1.40.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.39.0...v1.40.0) (2026-05-28)


### Features

* substrate admin read + mutation endpoints; remove admin visibility bypass ([8ea1d05](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8ea1d0521596bc6e71fe23d9fb58f6803484d428))

# [1.39.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.38.1...v1.39.0) (2026-05-27)


### Features

* enforce transcript uniqueness with idempotent re-ingestion ([7badb68](https://github.com/mini-app-polis/api-kaianolevine-com/commit/7badb68ab3d1ad199c84f11a6f73952436a35cae))

## [1.38.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.38.0...v1.38.1) (2026-05-27)


### Bug Fixes

* remove max_length caps from extraction request schemas ([0fbd5f4](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0fbd5f40147ecbad8bcb8223a33451e799778caa))

# [1.38.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.37.1...v1.38.0) (2026-05-27)


### Features

* automated migration application via scripts/apply_migrations.py ([be1c51c](https://github.com/mini-app-polis/api-kaianolevine-com/commit/be1c51c57325172786d5815a7e7c0f722ed35447))

## [1.37.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.37.0...v1.37.1) (2026-05-22)


### Bug Fixes

* testing and doc chnage only to address pipeline findings for DOC-010, DOC-007, DOC-006, and TEST-010 ([a54523d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a54523d6118cb84fd057f0223d7d6e22d3bc8340))

# [1.37.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.36.0...v1.37.0) (2026-05-20)


### Features

* WCS entity substrate endpoints — write, read, corrections, additions ([5b009ad](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5b009ad3b52302f3e3291266ae263e1f72de5434))

# [1.36.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.35.0...v1.36.0) (2026-05-20)


### Features

* WCS entity substrate foundation — models, schemas, composition service ([70bb326](https://github.com/mini-app-polis/api-kaianolevine-com/commit/70bb326fcc226326747193badad64fa085659742))

# [1.35.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.34.4...v1.35.0) (2026-05-20)


### Features

* migration for updated notes processing and design support docs ([2f613cb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/2f613cbe0451d2f51a2e474b7393413b3b476738))

## [1.34.4](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.34.3...v1.34.4) (2026-05-18)


### Bug Fixes

* allowing standards to be none in report ([11a3626](https://github.com/mini-app-polis/api-kaianolevine-com/commit/11a3626adec41f26ffe595407a87a3f43d6fc4aa))

## [1.34.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.34.2...v1.34.3) (2026-05-17)


### Bug Fixes

* addressing eval findings ([b8bc09f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b8bc09f1a66c5f44213282770cd855e0f40e5a0c))
* addressing eval findings ([46d18a5](https://github.com/mini-app-polis/api-kaianolevine-com/commit/46d18a52f90faaeb59f1e1e88184079d0d83de8a))

## [1.34.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.34.1...v1.34.2) (2026-05-17)


### Bug Fixes

* pipeline findings ([336f614](https://github.com/mini-app-polis/api-kaianolevine-com/commit/336f6143981f1c1fa24872a65d868edae61b49b8))

## [1.34.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.34.0...v1.34.1) (2026-05-17)


### Bug Fixes

* removing admin block for notes/all endpoint ([8c87e2f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8c87e2f6a831530c27610b640ded906ed2f351d3))

# [1.34.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.33.3...v1.34.0) (2026-05-15)


### Features

* adding not data editing ([4b4918e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4b4918e42bed02ba0b13c2de8eabdd5ba552e66f))

## [1.33.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.33.2...v1.33.3) (2026-05-12)


### Bug Fixes

* **evaluations:** coalesce evaluated_at with created_at and order most-recent-first ([237d877](https://github.com/mini-app-polis/api-kaianolevine-com/commit/237d877ca61936b072b5bcaab0ff184e6b9cfa40))

## [1.33.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.33.1...v1.33.2) (2026-05-11)


### Bug Fixes

* remove caching ([fe96a87](https://github.com/mini-app-polis/api-kaianolevine-com/commit/fe96a8706396f83e5a1d36d79fa7e0599db731e8))

## [1.33.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.33.0...v1.33.1) (2026-05-11)


### Bug Fixes

* adding price visibility ([8d46886](https://github.com/mini-app-polis/api-kaianolevine-com/commit/8d46886861d25c0da0d22d4bd37985ff938c2cdb))

# [1.33.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.32.0...v1.33.0) (2026-05-11)


### Features

* adding antropic cahcing ([e96def9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e96def9b6d9309b0031bf7136862f1c0d11e7318))

# [1.32.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.31.0...v1.32.0) (2026-05-11)


### Features

* providing estimated price information on prompts ([4f0670a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4f0670a42dc69a8a18b0c79f1e7e90c104adabbb))

# [1.31.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.30.2...v1.31.0) (2026-05-11)


### Features

* adding depth to the searches ([fe8b93f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/fe8b93ffbfe94528d9e2f526f2ec4db2e187cdb8))

## [1.30.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.30.1...v1.30.2) (2026-05-10)


### Bug Fixes

* eval findings warning ([93e2650](https://github.com/mini-app-polis/api-kaianolevine-com/commit/93e265074047b2659fc3e739d62c0e1b192ab05c))

## [1.30.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.30.0...v1.30.1) (2026-05-10)


### Bug Fixes

* models ([48f31b8](https://github.com/mini-app-polis/api-kaianolevine-com/commit/48f31b8da74f2de1e9c04c031dffb85d58cea3b6))
* models ([4471ebc](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4471ebc37f32ab5d8b9ab575e9f691b71bf5831f))
* tests ([f1607f9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f1607f98308bcc759f7631f0a052b96a08a82c3f))

# [1.30.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.29.0...v1.30.0) (2026-05-10)


### Features

* chunk citations link to underlying note instead of transcript reader ([42e8514](https://github.com/mini-app-polis/api-kaianolevine-com/commit/42e8514c07f88755520e4fcffe5461430f91b1af)), closes [#chunk-N](https://github.com/mini-app-polis/api-kaianolevine-com/issues/chunk-N)

# [1.29.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.28.3...v1.29.0) (2026-05-10)


### Features

* WCS Q&A agent with vector retrieval and eval harness ([451d94b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/451d94b775c640523a504dd7a2108a32301b8c64))

## [1.28.3](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.28.2...v1.28.3) (2026-04-22)


### Bug Fixes

* re-lock uv.lock and close AUTH-002 ([f297126](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f2971267bcdbe0badc2682b6385175fd1274d150))
* re-lock uv.lock in semantic-release prepare step ([6fadb2d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/6fadb2df9d6763a1e9a62b67379e52ed8c5e67fa))

## [1.28.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.28.1...v1.28.2) (2026-04-22)


### Bug Fixes

* migrate Railway builder from Nixpacks to Railpack ([b4f985f](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b4f985fe10b2d94529fafa0623f7f81bc95d97a3))

## [1.28.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.28.0...v1.28.1) (2026-04-22)


### Bug Fixes

* close pipeline findings (DOC-002, PRIN-005, TEST-013, TEST-010) ([1d5d715](https://github.com/mini-app-polis/api-kaianolevine-com/commit/1d5d715e81edc032ef8cb6adbc20633c6e604ce5))

# [1.28.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.27.2...v1.28.0) (2026-04-20)


### Features

* **config:** declare LOGGING_LEVEL, KAIANO_API_OWNER_ID, STANDARDS_VERSION, HTTP_CLIENT_TIMEOUT_SECS on Settings (CFG-001, CFG-002, TEST-013) ([4a4fc1d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4a4fc1d35177eb319d2a67a19ace255c026cda77))
* **main:** add /health liveness probe (API-010) ([941f26a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/941f26a6f3579589243e0af0e002ca57659465d8))

## [1.27.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.27.1...v1.27.2) (2026-04-20)


### Bug Fixes

* major doc update for evaluation findings ([5150dab](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5150dabf61f00f51e5abae153accc33dba77ba3c))

## [1.27.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.27.0...v1.27.1) (2026-04-20)


### Bug Fixes

* major doc update for evaluation findings ([dbd111e](https://github.com/mini-app-polis/api-kaianolevine-com/commit/dbd111e9041b3be948ead55e0c1cddd69889b5ef))

# [1.27.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.26.0...v1.27.0) (2026-04-18)


### Features

* reopening live music to public get ([ca2a4f0](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ca2a4f09bcbef7b65280c236aa7a5a94ec02c671))

# [1.26.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.25.1...v1.26.0) (2026-04-18)


### Features

* restore opaque token verification ([43cc08a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/43cc08aae035ebe94b84779b9aa669ce4d4751f4))

## [1.25.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.25.0...v1.25.1) (2026-04-17)


### Bug Fixes

* removing auth required for get flags ([61c5fd1](https://github.com/mini-app-polis/api-kaianolevine-com/commit/61c5fd1d92d9986ecb32854eef64c17b7773d43a))

# [1.25.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.24.0...v1.25.0) (2026-04-17)


### Bug Fixes

* tests to support current functionality ([b17cf57](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b17cf57ebdd2e3e793929bf435040d07a8df6b96))


### Features

* adding admin notes endpoint ([0752c1b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0752c1b31f8817c9801ee484fe659a0974c03ee5))
* finishing the proper implementation of auth through clerk ([5b89006](https://github.com/mini-app-polis/api-kaianolevine-com/commit/5b8900616d13d20f2749ad72a8413c02016de40b))

# [1.24.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.23.0...v1.24.0) (2026-04-17)


### Features

* upgrade common for auth ([80bffcd](https://github.com/mini-app-polis/api-kaianolevine-com/commit/80bffcd285b5a512cf831ad18db8db5b611a2124))

# [1.23.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.22.0...v1.23.0) (2026-04-17)


### Features

* upgrade common for auth ([798f22a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/798f22a6cb13ffe5c1f44a0aff0e73f5f1fce9cf))

# [1.22.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.21.1...v1.22.0) (2026-04-17)


### Features

* supporting full auth m2m pattern ([125e353](https://github.com/mini-app-polis/api-kaianolevine-com/commit/125e353cf36f97734030590acb1e063348fb28cc))

## [1.21.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.21.0...v1.21.1) (2026-04-16)


### Bug Fixes

* notes auth for admin specifically ([6898396](https://github.com/mini-app-polis/api-kaianolevine-com/commit/68983969a5858accd1ba8c03687d761d2ca93763))

# [1.21.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.20.0...v1.21.0) (2026-04-16)


### Features

* support for new auth for notes permissions ([1111254](https://github.com/mini-app-polis/api-kaianolevine-com/commit/11112547c17f7ff6e92c0f8ae07a844484ee5c3a))

# [1.20.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.19.0...v1.20.0) (2026-04-12)


### Features

* **evaluations:** expand severity to 5 values — add CRITICAL and SUCCESS with CHECK constraint ([3dd62e6](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3dd62e6fb08a83626820aec19c7a0fb102d4a7a6))

# [1.19.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.18.2...v1.19.0) (2026-04-10)


### Bug Fixes

* uv lock ([a9d65c3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a9d65c3000cbe82a957b417d16d26b6245ad3cc7))


### Features

* **evaluations:** add nullable violation_id field to pipeline_evaluations ([a95b02d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a95b02da53a21c1ee54e19e73fc7d65b50451dd9))

## [1.18.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.18.1...v1.18.2) (2026-04-10)


### Bug Fixes

* uv lock ([a1d7543](https://github.com/mini-app-polis/api-kaianolevine-com/commit/a1d75434b8dc28e144f5ef4c97c40cebb67eaceb))

## [1.18.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.18.0...v1.18.1) (2026-04-10)


### Bug Fixes

* migration details ([69707d7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/69707d76f067071a11a90ba73e7baee4f41e488a))

# [1.18.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.17.0...v1.18.0) (2026-04-10)


### Features

* add root URL redirect to /docs ([f2280e3](https://github.com/mini-app-polis/api-kaianolevine-com/commit/f2280e3281d2bfa2ecef2305ba750f8f7ac1595b))
* resolve real package version into FastAPI constructor and /version endpoint ([e46ba36](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e46ba3668e6ef46ee50cdcc785978f930f71b5c7))

# [1.17.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.16.2...v1.17.0) (2026-04-10)


### Bug Fixes

* uv lock ([4a87e1d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/4a87e1ddd78df68f798dbcb4dca637396bcf517c))


### Features

* add Project Keystone feature flags migration (013) ([0d63e91](https://github.com/mini-app-polis/api-kaianolevine-com/commit/0d63e91f1261a2093d1a00b24fb2caa15550d2a0))


### Performance Improvements

* replace Python years_active aggregation with SQL COUNT DISTINCT ([644b830](https://github.com/mini-app-polis/api-kaianolevine-com/commit/644b8306b645ab27859aea38cd327bbe78928a81))

## [1.16.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.16.1...v1.16.2) (2026-04-10)


### Bug Fixes

* add unique constraint and IntegrityError guard on concurrent set ingest ([934fe8a](https://github.com/mini-app-polis/api-kaianolevine-com/commit/934fe8a3d864f4843b487c24083a5b74bdd9d6a8))

## [1.16.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.16.0...v1.16.1) (2026-04-05)


### Bug Fixes

* owner id for api ([e56ae21](https://github.com/mini-app-polis/api-kaianolevine-com/commit/e56ae21e7f8e869c72096924a0eace324e9317c6))
* tests ([ec8f9fb](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ec8f9fbf971e545b95d8e3a24373e306b03e9def))
* tests ([781ff3b](https://github.com/mini-app-polis/api-kaianolevine-com/commit/781ff3b4f7cae47fb8e818fd86669db66a094e57))

# [1.16.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.15.2...v1.16.0) (2026-04-05)


### Features

* migration to more metadata on processed notes ([20f5034](https://github.com/mini-app-polis/api-kaianolevine-com/commit/20f503402a8bd725b7963067d62c91b374e46fe1))

## [1.15.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.15.1...v1.15.2) (2026-04-03)


### Bug Fixes

* revert ([095a901](https://github.com/mini-app-polis/api-kaianolevine-com/commit/095a9010feabdb94beb50d13d7c106f856ec6990))

## [1.15.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.15.0...v1.15.1) (2026-04-03)


### Bug Fixes

* railway deploy ([ab422a7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ab422a7984110b1994a6dc656c70c1bb1abf1461))

# [1.15.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.14.2...v1.15.0) (2026-04-03)


### Bug Fixes

* tests ([05710a4](https://github.com/mini-app-polis/api-kaianolevine-com/commit/05710a4f2c680d4442f4cf951aabf54048479f78))


### Features

* adding note ingest support ([b33cc26](https://github.com/mini-app-polis/api-kaianolevine-com/commit/b33cc26aa24ffb3cd47848ab4ec335357df4ec70))

## [1.14.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.14.1...v1.14.2) (2026-04-02)


### Bug Fixes

* updating sentry variable name ([3499d99](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3499d99386620ed825009cf565457a6d3da6c7c5))

## [1.14.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.14.0...v1.14.1) (2026-03-31)


### Bug Fixes

* applies filters to findings and summaries ([3aa8479](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3aa84794ca4f55e504c58d3da3e5b1d0cfa364b4))
* updates from comforance test findings ([ffc7bb9](https://github.com/mini-app-polis/api-kaianolevine-com/commit/ffc7bb9b5252d304e725213d93a429f2f03fa167))

# [1.14.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.13.2...v1.14.0) (2026-03-30)


### Features

* get version, error reporting, evaluations list ([693ba7d](https://github.com/mini-app-polis/api-kaianolevine-com/commit/693ba7d3768cc278b802d764e4e4a50221b0718a))

## [1.13.2](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.13.1...v1.13.2) (2026-03-29)


### Bug Fixes

* apply latest-run filter to evaluations summary ([43b3c99](https://github.com/mini-app-polis/api-kaianolevine-com/commit/43b3c99a4e4aa5e9e6676e60ff609648b9a2891b))

## [1.13.1](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.13.0...v1.13.1) (2026-03-29)


### Bug Fixes

* remove line length requirement ([aa203ee](https://github.com/mini-app-polis/api-kaianolevine-com/commit/aa203ee9f66df36d165e4edf72f97fe45cd63e4e))

# [1.13.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.12.0...v1.13.0) (2026-03-28)


### Features

* major support of spotify endpoint ([32431d7](https://github.com/mini-app-polis/api-kaianolevine-com/commit/32431d711123d72be01a95fb6bc6f82450251908))

# [1.12.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.11.0...v1.12.0) (2026-03-27)


### Features

* wire mini_app_polis for logging and music normalization ([3a6f8ee](https://github.com/mini-app-polis/api-kaianolevine-com/commit/3a6f8eef3eb3a1375744b74b7ff9eca0922756d2))

# [1.11.0](https://github.com/mini-app-polis/api-kaianolevine-com/compare/v1.10.0...v1.11.0) (2026-03-27)


### Features

* name and location restructure ([86c4c44](https://github.com/mini-app-polis/api-kaianolevine-com/commit/86c4c44a111471b2ba67728a45687f6839d0033c))

# [1.10.0](https://github.com/kaianolevine/api-kaianolevine-com/compare/v1.9.0...v1.10.0) (2026-03-27)


### Features

* reverting auth ([e464cee](https://github.com/kaianolevine/api-kaianolevine-com/commit/e464cee0a7793b2e6c421a5e8614671eb95457e4))

# [1.9.0](https://github.com/kaianolevine/api-kaianolevine-com/compare/v1.8.0...v1.9.0) (2026-03-27)


### Features

* pre commit cleanup ([1e84439](https://github.com/kaianolevine/api-kaianolevine-com/commit/1e844394f1b70a82823ddeebac086b82187d8dbb))

# [1.8.0](https://github.com/kaianolevine/api-kaianolevine-com/compare/v1.7.0...v1.8.0) (2026-03-27)


### Features

* adding clerk functionality ([3ad8a68](https://github.com/kaianolevine/api-kaianolevine-com/commit/3ad8a68442577ef9f7d44164377b3c25ff191b5c))

# [1.7.0](https://github.com/kaianolevine/api-kaianolevine-com/compare/v1.6.1...v1.7.0) (2026-03-26)


### Features

* adding resume endpoint to api from legacy api ([77f160c](https://github.com/kaianolevine/api-kaianolevine-com/commit/77f160cbff15fc52ec541450c0af6f61f25e5efd))

## [1.6.1](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.6.0...v1.6.1) (2026-03-26)


### Bug Fixes

* contact ([4f87db5](https://github.com/kaianolevine/deejay-marvel-api/commit/4f87db5f83a8f0e3058a2bf86ccf12ada4117e64))

# [1.6.0](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.5.1...v1.6.0) (2026-03-26)


### Features

* migrating legacy api contact to this api for final migration of legacy functionality ([f9568db](https://github.com/kaianolevine/deejay-marvel-api/commit/f9568db6099f6f481b9886e6bf98f447de2138f8))

## [1.5.1](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.5.0...v1.5.1) (2026-03-24)


### Bug Fixes

* adding duplicate detection at the api layer ([f8a54e6](https://github.com/kaianolevine/deejay-marvel-api/commit/f8a54e682e23531f9b7cc333833a861f7c1ff076))

# [1.5.0](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.4.0...v1.5.0) (2026-03-23)


### Features

* adding flow name information ([e29fee7](https://github.com/kaianolevine/deejay-marvel-api/commit/e29fee71e5d0cd6607d1c1d69ca175e5a274c5ee))

# [1.4.0](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.3.0...v1.4.0) (2026-03-23)


### Features

* migration + schema + both routers (evaluations and webhook) ([72f63d9](https://github.com/kaianolevine/deejay-marvel-api/commit/72f63d971633e5e2dd67a09a3d619891e86e4be4))

# [1.3.0](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.2.1...v1.3.0) (2026-03-23)


### Features

* Adding webhook endpoints for prefect ([41d05bb](https://github.com/kaianolevine/deejay-marvel-api/commit/41d05bbad64c0e1ee68ccfd4bba52179bd0e234b))

## [1.2.1](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.2.0...v1.2.1) (2026-03-23)


### Bug Fixes

* use pg_insert unconditionally in live plays router ([1455f73](https://github.com/kaianolevine/deejay-marvel-api/commit/1455f7324c3f6464b71b92e93f69e0ef9819abad))

# [1.2.0](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.1.0...v1.2.0) (2026-03-23)


### Features

* fix inner joins to handle nullable set_id for orphaned live tracks ([8e8f093](https://github.com/kaianolevine/deejay-marvel-api/commit/8e8f0935abf8fe64fbc3c76d4f838e774b1f7f8d))

# [1.1.0](https://github.com/kaianolevine/deejay-marvel-api/compare/v1.0.0...v1.1.0) (2026-03-23)


### Features

* add live play history ingest and recent plays endpoint ([3879193](https://github.com/kaianolevine/deejay-marvel-api/commit/3879193b086327b5b657d1601d08efb6f04cd697))

# 1.0.0 (2026-03-22)


### Bug Fixes

* add missing endpoints to CORS documentation ([3175cd6](https://github.com/kaianolevine/deejay-marvel-api/commit/3175cd6c506b61091e613eecdf93b538876c1d96))

# Changelog

## [0.3.1] - 2026-03-19
### Added
- track_count to SetListItem and SetDetail responses

## [0.3.0] - 2026-03-19
### Added
- CI workflow (lint + test on every push and PR)
- feature_flags table with three initial flags seeded
- GET /v1/flags and PATCH /v1/flags/{name} endpoints
- Flag check on ingest endpoint via is_enabled() service

## [0.2.0] - 2026-03-19
### Added
- Expanded pipeline_evaluations table with structured fields
  (run_id, finding, suggestion, standards_version, evaluated_at)
- POST /v1/evaluations accepts structured PipelineEvaluationCreate
- GET /v1/evaluations and GET /v1/evaluations/summary endpoints

## [0.1.6] - 2026-03-19
### Added
- CORSMiddleware configurable via CORS_ORIGINS environment variable

## [0.1.5] - 2026-03-19
### Added
- track_count field on SetListItem and SetDetail responses

## [0.1.3] - 2026-03-18
### Changed
- Redefined data_quality to use 4 enrichment fields only
  (genre, length_secs, bpm, release_year)

## [0.1.2] - 2026-03-18
### Added
- Historical data migration script (now removed — replaced by
  POST /v1/admin/migrate endpoint pattern)

## [0.1.1] - 2026-03-18
### Added
- Missing CSV columns: remix, label, comment
- Fixed play_order nullability

## [0.1.0] - 2026-03-18
### Added
- Initial implementation. FastAPI service with PostgreSQL via
  SQLAlchemy async. Five tables: sets, tracks, track_catalog,
  pipeline_evaluations, feature_flags. All endpoints implemented.
  Reconciliation and normalization services. Contract tests for
  all endpoints.
