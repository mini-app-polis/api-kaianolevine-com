-- Migration 039: honour the Remix column on plays from 2022 on
--
-- From 2022 the set files carry an optional Remix column beside Title. The
-- retired ingest dropped it: every play was stored with remix empty and
-- filed under its plain title, so "Temple" with "Niko The Kid Remix" in the
-- Remix column counted as the original Temple. 294 plays in the 2022 and
-- 2023 set files have the column filled; 288 are on record (the other six
-- belong to 2023-02-04 Charlotte Westie Fest, a set that is not on record).
--
-- The rule, agreed 2026-10-15: the Remix column is optional, but when it is
-- filled it is not ignored.
--   * Every one of those plays gets its Remix value recorded on the play.
--   * A version (a remix, acoustic, live, stripped, session or recording
--     like "Recorded at Spotify Studios NYC") files the play under that
--     version's catalog entry, "Title (Version)", joining the entry when the
--     catalog already has it (matched on title + version ignoring case and
--     punctuation, and on the main artist) and creating it when not.
--   * A release label (Clean, Explicit, Edited, Album Version, Radio Edit,
--     Original Mix, Main, a remaster or "2020 Mix", a plain "Cover", a
--     feat. credit, or a routine/file note) only records the value: the
--     play stays with the song, as for labels written in the title.
-- Afterwards every catalog entry these plays touched has its play count and
-- first/last played recomputed from its plays, and an entry left with no
-- plays is deleted.
--
-- Keyed on production play ids: a database without them changes nothing,
-- and a re-run finds the plays already filed and changes nothing.

CREATE TEMP TABLE remix_backfill (
  track_id uuid, remix text, version text, kind text
) ON COMMIT DROP;

INSERT INTO remix_backfill (track_id, remix, version, kind) VALUES
  ('3d8dceaf-2c1c-4fa4-8ce6-bbf9be959e5e', 'Cover', 'Cover', 'label'),
  ('ac15d3d1-e6fe-40f8-bd55-cc0eeaefbe49', 'Acoustic', 'Acoustic', 'version'),
  ('a05c97f7-d1f4-49e2-8683-e763b77ace49', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('53c4d01a-1587-4af5-9ac9-e60de90a67d2', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('1e634da5-dc04-42d9-b34d-8888fb5fa338', 'Vasco Remix', 'Vasco Remix', 'version'),
  ('a507e7a5-19d2-4bc7-a9a1-35c4f0c78b98', 'Radio Edit', 'Radio Edit', 'label'),
  ('925f97d9-9df5-4b10-96c5-aeec4da95135', 'Niko The Kid Remix', 'Niko The Kid Remix', 'version'),
  ('c2855c64-b0f0-493a-b449-c4555b759888', 'Ryan Riback Remix', 'Ryan Riback Remix', 'version'),
  ('a2f5ca82-3efa-4516-ac99-0a14c8e03cbe', 'Remix', 'Remix', 'version'),
  ('2157e627-00d7-4731-b134-e2f873018cc3', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('20e81ba5-e6cf-4268-a194-590ef0e7f23f', 'Radio Edit', 'Radio Edit', 'label'),
  ('c45f6f43-0eca-46f2-834d-193ea9cc5ad5', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('f1643654-df44-413f-abc1-a851aed5310c', 'Main', 'Main', 'label'),
  ('f3d08388-8ba2-4ed1-ac7e-e49b31d4c599', 'Acoustic', 'Acoustic', 'version'),
  ('13bb4c14-d71e-4215-abac-8b531a17f901', 'Acoustic', 'Acoustic', 'version'),
  ('617e44d9-1c73-4c25-ada5-093823faba9b', 'Acoustic', 'Acoustic', 'version'),
  ('547c191e-f7ec-42ff-b1f7-87581a82f3fd', 'Acoustic', 'Acoustic', 'version'),
  ('11735cdb-a40d-47c3-b1f5-b9e717a9b5d0', 'Acoustic', 'Acoustic', 'version'),
  ('5f0c2e5c-3002-4bd5-96b0-15f142ab164c', 'Acoustic', 'Acoustic', 'version'),
  ('3b1bcc8b-d113-4e78-934b-50fe55e943bb', 'Acoustic', 'Acoustic', 'version'),
  ('7bae3169-9f70-4851-82c2-9e6cbf347392', 'Acoustic', 'Acoustic', 'version'),
  ('76fa7f58-eae0-4ba6-848c-9e3f97cefc2b', 'Acoustic', 'Acoustic', 'version'),
  ('cdddd25d-7ea1-43af-b431-565463ad74b6', 'Acoustic', 'Acoustic', 'version'),
  ('813bd2db-45b7-4e0f-b49c-3dd7e431ea52', 'Acoustic', 'Acoustic', 'version'),
  ('61f8f36a-eacd-4bbf-b70a-7ab436809126', 'Acoustic', 'Acoustic', 'version'),
  ('23b211ed-e5c4-4032-b316-30d514d87450', 'Pnau Remix', 'Pnau Remix', 'version'),
  ('97a7633b-b66f-415c-96a8-d82b00f206ad', 'Radio Edit', 'Radio Edit', 'label'),
  ('af116b29-3225-4e09-8a17-030f3cf6a5d3', 'Le Boeuf Remix', 'Le Boeuf Remix', 'version'),
  ('54cf3c6d-15e7-4c28-9d25-f5f06a46b8a0', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('3221957d-4c9f-43ba-9753-eb358fc155cf', 'Album Version', 'Album Version', 'label'),
  ('4d4dcb01-d644-4c24-b4ea-67d4874f2b95', 'Acoustic', 'Acoustic', 'version'),
  ('22643bbc-7ff5-4027-a85b-d7bc96a53205', 'Acoustic Live', 'Acoustic Live', 'version'),
  ('163d5bb2-4319-4872-8567-ed0d8d6c6072', 'Acoustic', 'Acoustic', 'version'),
  ('898f13c7-d785-453f-9849-b97747883fc3', 'Acoustic', 'Acoustic', 'version'),
  ('e9bc5ac3-1172-4a3f-b714-a9ca287fd177', 'Acoustic', 'Acoustic', 'version'),
  ('ceac0d87-002d-412a-9c67-5d820c3fcbbb', 'Acoustic', 'Acoustic', 'version'),
  ('18175321-fdba-491e-bb5e-13e306ae97eb', 'Acoustic', 'Acoustic', 'version'),
  ('84985730-37fc-47c3-b37b-7976c649be35', 'Pnau Remix', 'Pnau Remix', 'version'),
  ('75e911bf-29af-4ec9-b1d3-123a0e294759', 'Radio Edit', 'Radio Edit', 'label'),
  ('56af49fd-fe9b-4190-97e3-58a872b463f3', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('f23ef184-37a8-4c33-862b-0ff2d57c7dd0', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('065fd363-f4bc-4097-863f-fa6faa411ba7', 'Remix', 'Remix', 'version'),
  ('8da4d38d-8a04-4433-ad9e-0b87dd37691a', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('a699628b-a15a-43e7-9c81-7a0c2a0423ff', 'Live', 'Live', 'version'),
  ('03343f80-b918-4965-97da-dee87c66261a', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('e2012b92-19a8-4a4b-8a62-d9861cbd1b26', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('d314bd3a-3073-423e-9ee4-b3b47c0af6c4', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('d06115a8-b5f6-4171-a68b-4461c5bfcef4', 'Swan.g Intro Edit', 'Swan.g Intro Edit', 'version'),
  ('6c655f02-d86b-45af-bc93-ba6e9f8b4f7e', 'Remix', 'Remix', 'version'),
  ('32a470b7-e751-4a21-935b-c4d90ef347f3', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('59db33db-39d4-4d89-a11c-a06ce33598b1', 'Stripped Back', 'Stripped Back', 'version'),
  ('2a98ca6f-23d9-43fa-bf99-6362c28cd9eb', 'Dawin Remix feat. Christopher & Dawin', 'Dawin Remix feat. Christopher & Dawin', 'version'),
  ('431a57e2-31e6-4a70-aeaf-30c246e266b0', 'Vanboii Remix', 'Vanboii Remix', 'version'),
  ('49fc5ebe-6f9a-4c1a-8461-06bde2ed802c', 'Stripped Back', 'Stripped Back', 'version'),
  ('83a66ff2-fbc8-4a39-aba2-2412e07d0aae', 'No Intro Edit', 'No Intro Edit', 'label'),
  ('38b4ddec-ac98-483f-a235-2d50d65b916e', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('a75511b0-6339-41f7-a48e-c9b2990930b1', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('df2ce4ec-9dc8-417e-bc55-6a13d869caef', 'The Purgatory Sessions', 'The Purgatory Sessions', 'version'),
  ('a0109fa5-5510-4fd8-8f08-f68e9407b8b5', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('655f4327-a6bc-43db-aeaf-f713e268f58d', 'Acoustic', 'Acoustic', 'version'),
  ('e7859a6d-b234-4763-81d6-8391853f705e', 'Ryan Riback Remix', 'Ryan Riback Remix', 'version'),
  ('e852d20a-c88f-4f34-9a8c-649230158143', 'Radio Edit', 'Radio Edit', 'label'),
  ('4e93f450-6cd1-47fc-8d0d-b9c72ac7a51f', 'Stripped', 'Stripped', 'version'),
  ('385654d5-3f74-44fa-8839-fb082d7aef31', 'Guitar Acoustic', 'Guitar Acoustic', 'version'),
  ('fd93960f-181c-45c0-8bb5-333cec90f71d', 'Stripped Back', 'Stripped Back', 'version'),
  ('fe6df8d5-f973-40ea-93d5-ffabcfeaedfe', 'Cover - Recorded at Spotify Studios New York City', 'Recorded at Spotify Studios New York City', 'version'),
  ('5f21d14a-ada5-4c19-9a81-6df072536cde', 'Remix', 'Remix', 'version'),
  ('6552d958-ba9b-4608-9624-6d083ff01277', 'Radio Edit', 'Radio Edit', 'label'),
  ('aa56115b-21ed-49cb-ab1f-17534b1ce927', 'Swan.g Intro Edit', 'Swan.g Intro Edit', 'version'),
  ('b9cfb7d4-c51a-4baf-8bcc-eae5cb1ad7cc', 'Cover Remix', 'Cover Remix', 'version'),
  ('1c6c40f0-2d45-4c03-9e95-fc13013b852c', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('44da1340-454c-4d15-bec9-76652a5f69cc', 'Acoustic', 'Acoustic', 'version'),
  ('e24cc9b5-abe0-4009-862b-b7682c56c6d6', 'Acoustic', 'Acoustic', 'version'),
  ('e2c9e16e-e5e5-454b-9449-2443c0620d7b', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('859882b4-04cc-4622-974f-1d0a4b479155', 'Radio Edit', 'Radio Edit', 'label'),
  ('c721d838-09d8-4995-b741-67b0679d433e', 'Acoustic', 'Acoustic', 'version'),
  ('61e973a9-65bd-448d-be08-0da7d19eb387', 'Acoustic', 'Acoustic', 'version'),
  ('91046522-4f82-4be1-8926-11bee8f6743e', 'Analog Sessions', 'Analog Sessions', 'version'),
  ('18271e21-dd97-46cc-8602-d988f8820b64', 'Acoustic', 'Acoustic', 'version'),
  ('e9922426-6a7b-4b26-ab17-d36ca3cbaa42', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('8a85de83-5b34-42ab-a2d3-4e1dda1c2bc0', 'Radio Edit', 'Radio Edit', 'label'),
  ('3760f18a-a59f-4722-865c-a61791b0c698', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('892f6a45-d4ed-42b5-89a1-008a1745ff84', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('1aad4b1a-a484-48db-834b-a6732ae2e676', 'Acoustic', 'Acoustic', 'version'),
  ('534041e2-efce-46d3-97fb-cbfab60d77cd', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('dd6d68f0-496a-48e5-b390-75af2954fac0', 'Cover', 'Cover', 'label'),
  ('32981443-bf7f-4877-ae48-ce51e750531e', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('c82c63df-11f5-4823-965b-b513cf935c80', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('ac83babf-0e5b-43de-8847-b2156bc67833', 'Basic Radio', 'Basic Radio', 'label'),
  ('801ff903-59af-4971-be6a-ff12bd643a7e', 'Just Dance Version', 'Just Dance Version', 'version'),
  ('3e2aadc8-0d0f-4813-b841-a73ccc799122', 'Main', 'Main', 'label'),
  ('4bd94ac2-7a45-4fca-8e9b-5fe51d23ef8e', 'Backstreet''s Back', 'Backstreet''s Back', 'label'),
  ('e6c13ef8-2cf7-47b4-8d94-add9a261e10f', 'Basic Radio', 'Basic Radio', 'label'),
  ('0d12a0c8-0bae-49fc-9ae5-bdea1f934b4e', 'Basic Radio', 'Basic Radio', 'label'),
  ('bcd22780-e92b-480a-90fe-bbf70ae0d257', 'Acoustic', 'Acoustic', 'version'),
  ('cbfa3d48-5985-4d9a-b1de-9623415ea761', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('56e5f2ef-afcf-4137-9ba5-03482bbf5a9f', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('e9b4d72e-5f5d-4e86-9bfc-4b598e1e2ca8', 'Edited', 'Edited', 'label'),
  ('e2d7aecf-0a7b-4b2e-b229-78657c3f6ded', 'The Purgatory Sessions', 'The Purgatory Sessions', 'version'),
  ('f6c681a8-fb3f-4d0f-a990-9c3f0fb63a4d', 'Acoustic', 'Acoustic', 'version'),
  ('8b9665d6-d717-4314-ba0b-06c8f4272752', 'Acoustic', 'Acoustic', 'version'),
  ('8f551672-d499-42d8-988f-8f4a2a2f9386', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('c3628fd3-4a5c-4f59-acb6-08cbf2eae2dc', 'Album Version', 'Album Version', 'label'),
  ('35b3a6cb-33e6-45d8-ba68-ef3be6690486', 'Pnau Remix', 'Pnau Remix', 'version'),
  ('ab7d1fab-7a9c-4ec8-840e-d446864d0e9b', 'Abi routine final', 'Abi routine final', 'label'),
  ('e88ede77-b647-4684-b12b-2632ad074270', '320kbps4secIntro', '320kbps4secIntro', 'label'),
  ('3ef87362-02e5-42c8-993c-2fcf18fbb718', 'Drowning 100  tempo', 'Drowning 100  tempo', 'label'),
  ('95b57d0c-0a22-4b6d-bb66-a1f9609408df', 'Abi routine final', 'Abi routine final', 'label'),
  ('79725ccc-68cc-4cdc-9b02-d3d8b4d93eaf', 'Drowning 100  tempo', 'Drowning 100  tempo', 'label'),
  ('4abcf760-cd8a-4712-94eb-dff54423e8b5', '320kbps4secIntro', '320kbps4secIntro', 'label'),
  ('5086f01a-18f2-44a3-877c-ae1aa08d9041', 'Drowning 100  tempo', 'Drowning 100  tempo', 'label'),
  ('14792ed5-98f6-4896-8464-b91294818503', 'Abi routine final', 'Abi routine final', 'label'),
  ('e5cc80b1-1caf-42a5-bf44-7c0db989dbe5', 'R3HAB Remix', 'R3HAB Remix', 'version'),
  ('b546cc6a-0d83-4424-8a30-95b4b859c8ee', 'The Purgatory Sessions', 'The Purgatory Sessions', 'version'),
  ('ef4136b8-613f-4dfa-8f19-72fa7b4ae878', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('0aa6ac7c-3750-42c9-b49a-29f713eccd6a', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('c00d9ff7-3f14-4a95-b462-682fa229616f', 'Marian Hill Remix', 'Marian Hill Remix', 'version'),
  ('d85564e4-1cba-4770-bdce-36c401f99efb', 'Acoustic', 'Acoustic', 'version'),
  ('cd028d7a-156f-4a30-8e63-48b46522bbbf', 'Stripped', 'Stripped', 'version'),
  ('22b88a75-673c-469d-810b-7d03c22d940f', 'Aquilo Remix', 'Aquilo Remix', 'version'),
  ('78efdd70-495e-4be5-9aff-915d171adba3', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('ca6b9797-b6f6-47fb-8cec-55004d27c6eb', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('7d883768-a387-4df5-9fbe-3e12ebada2dc', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('ed04e7c5-df2d-44bf-b9d7-e61ca314ae20', 'From The Still Working 9 To 5 Documentary', 'From The Still Working 9 To 5 Documentary', 'label'),
  ('64f36469-9a67-436b-aa81-c6a17e0bac37', 'Cover - Recorded at Spotify Studios New York City', 'Recorded at Spotify Studios New York City', 'version'),
  ('5a9a3503-3b47-4998-a25e-a7ac65f76778', 'Original Mix', 'Original Mix', 'label'),
  ('226a5210-e2d5-4fcb-910f-b3fd52b5f09c', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('b461c1a0-a54a-43d9-ad0b-50b0eb318946', 'Cover', 'Cover', 'label'),
  ('52e19555-57d5-4dd9-9d90-fc6b529e6253', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('be73f58a-3529-4ccc-9d91-519f597cba77', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('72f6d0da-1013-4a2f-b518-49e930241dd2', 'Acoustic', 'Acoustic', 'version'),
  ('c26e1ab6-d408-499e-9d5d-cbc5a623dee4', 'Pnau Remix', 'Pnau Remix', 'version'),
  ('90324ee0-ed93-4d2e-965e-af377aeb2e15', 'Cover - Recorded at Spotify Studios New York City', 'Recorded at Spotify Studios New York City', 'version'),
  ('48f48a33-7a89-4eae-afd9-5470cecb9f3e', 'From The Still Working 9 To 5 Documentary', 'From The Still Working 9 To 5 Documentary', 'label'),
  ('95143586-bbb0-4ac7-9fb7-e99681623ff1', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('58d021f7-9ca4-4284-965f-d5a627dfee94', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('5cb72a4f-9f51-42d4-8ee5-42f36b2ba3f9', 'Edited', 'Edited', 'label'),
  ('c2c17ba1-a005-452c-8696-4e308ddb3809', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('18ba4b86-ca44-49bc-9287-072fb24274a6', 'Stripped', 'Stripped', 'version'),
  ('ef9ba49c-a468-4161-baf2-9b2bbf4b39b0', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('aa77bbbd-ae7d-4236-82e2-ee1505ea319e', 'Vanboii Remix', 'Vanboii Remix', 'version'),
  ('ac2ec986-c86d-409b-baed-b00dfdc9a67d', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('6da684af-74cb-43d6-86ef-531e67d34a77', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('3b388186-0430-489b-bc65-ad203c44bc81', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('4f3639ea-f2ef-47be-9ff1-de3f2c3dde84', 'Le Boeuf Remix', 'Le Boeuf Remix', 'version'),
  ('0f2d21f6-bfc7-41af-9c17-910fcc4228ba', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('3a37d9b3-90e6-4f4d-a40c-59797d48d499', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('6d821a49-1fbb-4da8-acc7-f22111081732', 'Edited', 'Edited', 'label'),
  ('1ef21c66-df73-4fb1-bfc0-fb868647c8d0', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('13fc8d1c-9f92-41dd-a0dd-415f61a6a6f8', 'Swan.g Intro Edit', 'Swan.g Intro Edit', 'version'),
  ('96472d33-a911-42d0-92bb-ffc40988ceb1', 'From The Still Working 9 To 5 Documentary', 'From The Still Working 9 To 5 Documentary', 'label'),
  ('bc945315-a64a-4622-b48c-a125744d0126', 'Acoustic', 'Acoustic', 'version'),
  ('3fefba0f-d64b-4c30-a0a9-9c06940015e1', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('d7add83f-5dae-4252-b84f-afc407ba0c77', 'Stripped', 'Stripped', 'version'),
  ('039a9dfe-770e-46cb-b967-fe3b458941b4', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('6125483e-6b1d-4eaa-b864-e3706c2f551d', 'Explicit', 'Explicit', 'label'),
  ('6b17060f-42b9-457d-98e2-74a1d5a02eac', 'Acoustic', 'Acoustic', 'version'),
  ('a3d7a286-fa7b-40c0-8efb-8f2468e9fcf3', 'Le Boeuf Remix', 'Le Boeuf Remix', 'version'),
  ('2f47f636-e328-4650-9828-c342ef38430c', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('4b49fe31-bc8a-4ca7-a4f6-17011d831d5e', 'edit', 'edit', 'label'),
  ('7c9565f2-cc18-4c1a-9e33-2e9c1345eddf', 'Mixed', 'Mixed', 'label'),
  ('fd1cc2ca-c9a5-4d4d-b904-8abcb272fb1f', 'Vanboii Remix', 'Vanboii Remix', 'version'),
  ('3f736273-f1df-4b48-85eb-8484364e19d0', 'The Purgatory Sessions', 'The Purgatory Sessions', 'version'),
  ('37b69b50-bb28-49d2-bdd6-b31f41ac7a09', 'Acoustic Live', 'Acoustic Live', 'version'),
  ('82bba8bc-a010-45c5-90e0-8a59eb910067', 'RLR Remix', 'RLR Remix', 'version'),
  ('471f7f7c-8ac8-4283-bacb-fe6dc9279a9b', 'Acoustic', 'Acoustic', 'version'),
  ('aed9f2d1-4811-424e-9eb6-22bdf3a69d52', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('c0d55bc1-92b7-4de7-a4f6-0d9cd1dbcd30', 'Edited', 'Edited', 'label'),
  ('ab776837-1c86-4bb8-8e48-d94ede9231bf', 'Radio Edit', 'Radio Edit', 'label'),
  ('7ef39a86-61d5-488b-a077-ca80968379e5', 'Remix', 'Remix', 'version'),
  ('19fdc131-1c08-4560-b5fe-c8cfcb55467b', 'edit', 'edit', 'label'),
  ('8d219a84-191e-4c51-bb3c-770f1166afab', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('b17edb1f-8a5b-4248-9583-7f862149f82d', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('53c32932-dc84-4955-af4d-e25f3df75e15', 'Acoustic', 'Acoustic', 'version'),
  ('721a74d0-f052-464a-8b14-7594a5bf2800', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('01da9a29-ac63-4a09-89cb-442826711a29', 'Acoustic', 'Acoustic', 'version'),
  ('2463e2fa-a88e-490f-b1d6-278b8bbc3397', 'VF', 'VF', 'version'),
  ('4f97e781-f6da-4851-a078-aa46494bb73e', 'Radio Edit', 'Radio Edit', 'label'),
  ('ca11830b-e408-452a-a1bc-bfc4b30d9e22', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('a7d1e154-2835-424d-83bf-536efebb4bbd', 'Swan.g Intro Edit', 'Swan.g Intro Edit', 'version'),
  ('437fe5a2-3433-4acc-b1f6-01882b8425ff', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('3e35630d-566d-4a30-955d-0e023b67e85f', 'Aslove Remix', 'Aslove Remix', 'version'),
  ('59130907-d994-4e65-8019-87dbc44810a7', 'edit', 'edit', 'label'),
  ('6de9fee1-0be6-4e19-b37d-a311fdfffa77', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('880c670f-0267-4755-953a-5599e5b7c955', 'Acoustic', 'Acoustic', 'version'),
  ('c5e39dd6-0ade-4523-b9b2-9143ccf22bd8', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('34db96c2-c0c2-47e4-80ae-a874cd9da140', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('889ee2de-d29a-45b9-8db2-a83b089decdf', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('30b0938a-3c3a-4efa-9d0f-7cb7188bdeb3', 'Acousrtic Cover', 'Acousrtic Cover', 'version'),
  ('1eb16ba6-a95b-43ec-bebb-787ec5a2e374', 'Album Version', 'Album Version', 'label'),
  ('8b88ecaa-c71c-4c05-ba32-a78bbd9601d4', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('6978266f-da09-4662-b561-229ae5c20a02', 'FKJ Remix', 'FKJ Remix', 'version'),
  ('ac20681d-52f1-40ec-af39-c3a510a8dd5e', 'edit', 'edit', 'label'),
  ('e26e1dd7-f478-454a-bdf0-76dc79b6dc47', 'Mixed', 'Mixed', 'label'),
  ('72923914-8222-4d42-96e1-18502b641442', 'Radio Edit', 'Radio Edit', 'label'),
  ('6843f3af-546d-46f6-a823-4baba06e4543', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('786e7fe8-cc0f-444b-ae02-56b745da9227', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('38ae5c59-14c7-4a0f-89e9-d95d4f83a97b', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('01c6f79f-a1c7-4d49-aad2-85a1026629ae', 'Radio Edit', 'Radio Edit', 'label'),
  ('e45ccb2b-5e85-4b24-8bdf-4f6ac01243e3', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('d3fa570d-3ff6-4945-a081-e7c38e28453b', 'filous remix', 'filous remix', 'version'),
  ('64d14596-220a-4fcc-871f-4b460a258eaf', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('e70e228e-27a3-4476-b268-612e9714049e', 'Mixed', 'Mixed', 'label'),
  ('c58f75c5-7043-4438-bfbf-1e652bb4652f', 'Basic Tape Remix', 'Basic Tape Remix', 'version'),
  ('88d18453-0f2b-48d1-8fbd-49bcbd867520', 'Pnau Remix', 'Pnau Remix', 'version'),
  ('2c607676-decd-4513-baa0-57795cf56cec', 'Radio Edit', 'Radio Edit', 'label'),
  ('927cea53-ff0d-42ad-aa8c-96aeae49aa20', 'Backstreet''s Back', 'Backstreet''s Back', 'label'),
  ('d5ba8f36-450f-48aa-ba79-e3e481b5e1a7', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('5cb99c43-981b-4b75-b637-d42aaa35a02c', 'Radio Edit', 'Radio Edit', 'label'),
  ('44bad229-bb73-49e4-9611-96413b967034', 'Remix', 'Remix', 'version'),
  ('36c668a5-d0e1-481c-8462-c57a2550772a', 'Live Acoustic', 'Live Acoustic', 'version'),
  ('4e5c73db-b9d9-4d0a-8ca4-50935f078996', 'Guitar Acoustic', 'Guitar Acoustic', 'version'),
  ('23440608-fce6-436d-8e74-4483a913e1d8', 'Radio Edit', 'Radio Edit', 'label'),
  ('daf313c1-a655-4dd9-a1cd-14799501e696', 'Acoustic', 'Acoustic', 'version'),
  ('6d495d4b-2f97-45a8-a335-f7dc47682b27', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('98780cc9-8ec8-488b-8cfa-863e6be3e7c3', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('c6b254ae-b425-4e50-83c2-a99f35f33f1b', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('8e5535e5-cac9-4426-9499-e7bee12afe33', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('3cd88b6a-7e68-4e8b-b999-247104bc1c1b', 'Cover - Recorded at Spotify Studios New York City', 'Recorded at Spotify Studios New York City', 'version'),
  ('b8fe3295-7754-44d5-8927-8e447237b598', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('a19a08df-a983-4ebb-a262-da37e823da36', 'SpiMan: Into The Spider-Verse', 'SpiMan: Into The Spider-Verse', 'label'),
  ('d0268c08-9f96-4b73-a9ff-9ba077f38414', 'Radio Edit', 'Radio Edit', 'label'),
  ('6616f761-faf6-4541-9271-9047b484de67', 'Alan Walker Remix', 'Alan Walker Remix', 'version'),
  ('5ff90814-ac95-481d-8769-05c556361ca8', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('7f01088c-48c4-4994-a3c9-7193988c4457', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('f8bc329a-7036-4321-8423-fe5b07cd3067', 'Acoustic', 'Acoustic', 'version'),
  ('ab44b263-9779-4be1-852e-dea9d6636874', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('2c3e428a-ed0c-4f83-8fdc-37e898540ef5', 'Acoustic Live ?', 'Acoustic Live ?', 'version'),
  ('f151b7d4-e32b-48c9-b554-d8067135fca4', 'From The Still Working 9 To 5 Documentary', 'From The Still Working 9 To 5 Documentary', 'label'),
  ('dd1676ff-cfe8-4db7-bceb-4cae1f8e1cd6', 'Le Boeuf Remix', 'Le Boeuf Remix', 'version'),
  ('90592a5f-6523-49c9-acc1-4975f101da89', 'Acoustic', 'Acoustic', 'version'),
  ('a9c39d42-3e80-44ac-a469-c5834e17f483', 'Acoustic', 'Acoustic', 'version'),
  ('eeeff1be-9733-4172-bb40-e8208b654218', 'Remastered 2008', 'Remastered 2008', 'label'),
  ('2f201df3-6e02-4a50-9d85-600855ba56b4', 'Mixed', 'Mixed', 'label'),
  ('8273ac2b-462e-4acc-86cb-7914d0bab096', 'Radio Edit', 'Radio Edit', 'label'),
  ('afa51fe3-a3fb-4a87-a933-620f68d036e1', 'Radio Edit', 'Radio Edit', 'label'),
  ('1728dbb2-e9ac-402a-a0d1-b9688948b534', 'NoMBe vs Sonny Alven Remix', 'NoMBe vs Sonny Alven Remix', 'version'),
  ('ee02947a-2822-401b-851a-033c50ab0c90', 'Radio Edit', 'Radio Edit', 'label'),
  ('ce30685d-f2f0-4f4d-811b-2a86c12e3a80', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('60a27bfe-6ca7-4dd2-a823-df2c5dbd6338', 'Piano Acoustic', 'Piano Acoustic', 'version'),
  ('adac5e6a-8ed7-4838-b209-3e5952d8443a', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('61a03b5b-5c17-4aa4-84c4-686e1aa3e996', 'Pnau Remix', 'Pnau Remix', 'version'),
  ('53967a19-5244-47c8-8e44-b0d05466916c', 'Radio Edit', 'Radio Edit', 'label'),
  ('abdb266d-250c-44c9-9f02-a7e1b3f6fce1', 'Swan.g Intro Edit', 'Swan.g Intro Edit', 'version'),
  ('dea0f163-130a-40dc-981a-900cf6a602b7', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('0edfcd4e-16b7-4ec8-9609-6fe98f9cca0f', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('cebd5bd4-ed31-4aca-a565-7da913435c7b', 'Lenno Remix', 'Lenno Remix', 'version'),
  ('2420145a-7a43-4e60-b104-9c5d4f683cd0', 'The Purgatory Sessions', 'The Purgatory Sessions', 'version'),
  ('3f0d6a0a-b44d-4cd2-9255-5553d1cb7db7', 'Mixed', 'Mixed', 'label'),
  ('aceaeadd-51ca-4c6e-8648-ba434f616360', 'feat. Offset', 'feat. Offset', 'label'),
  ('8343d57c-46d6-4f90-9fbe-b43e289cb3c3', 'Sistek Remix', 'Sistek Remix', 'version'),
  ('efaebf72-54be-4469-bdf8-20e4afc7a4b0', 'Radio Edit', 'Radio Edit', 'label'),
  ('6d7e19fc-4a78-40e8-9fb9-38647389c603', 'intl. Version', 'intl. Version', 'version'),
  ('0d2d7fdc-7a41-449f-8c4a-99be3e285b2b', 'Explicit', 'Explicit', 'label'),
  ('6ef3560d-78ff-4bf2-9955-8faae8de0ac8', '2020 Mix', '2020 Mix', 'label'),
  ('d339e6da-a3ab-491d-a0ec-1db483c63b50', 'Radio Edit', 'Radio Edit', 'label'),
  ('073d17d7-0c3e-407b-9cd3-967ef7a33dd3', 'intl. Version', 'intl. Version', 'version'),
  ('effecaf3-6f5b-4282-bfd5-1dc0486243be', 'feat. Offset', 'feat. Offset', 'label'),
  ('3770958e-570e-4991-8039-372a6c1961e2', 'Radio Edit', 'Radio Edit', 'label'),
  ('0a6d0a5f-0cc1-4ca5-b34b-1be27657faf4', 'Explicit', 'Explicit', 'label'),
  ('bca8657d-3eb9-4c6b-b143-520cf45f7414', 'Penguin Prison Remix', 'Penguin Prison Remix', 'version'),
  ('53acfba0-0782-41ec-8106-1dd9fd32ee01', 'Aslove Remix', 'Aslove Remix', 'version'),
  ('7f2b9a60-6466-4783-92ec-08f1ecb000ab', 'Clean', 'Clean', 'label'),
  ('406ea884-f580-471e-9cd5-55e33a8e7ba0', 'Excuse Me Miss', 'Excuse Me Miss', 'label'),
  ('98f8896b-d351-4d23-acfc-0d54da72715b', 'Explicit', 'Explicit', 'label'),
  ('5b447937-fc5b-41f5-af36-6f2786d5283c', 'Explicit', 'Explicit', 'label'),
  ('27575ec4-fe2a-49a9-9035-234350e39c8a', 'Radio Edit', 'Radio Edit', 'label'),
  ('f4513718-726e-4a62-8b69-b7556fa69ca0', 'Explicit', 'Explicit', 'label'),
  ('3c8acb8b-7cf2-4231-bdbf-2520c7891704', 'Acoustic', 'Acoustic', 'version'),
  ('a23fd692-9e0f-466c-9bb1-c76d92a75e54', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label'),
  ('50b335b4-4f58-4ab1-8d06-d2c7f590904f', 'Backstreet''s Back', 'Backstreet''s Back', 'label'),
  ('8007a33c-479a-48ea-88b7-e78256f52d35', 'Clean', 'Clean', 'label'),
  ('3a9cc282-d110-48c3-8401-e3a811ee418b', 'Explicit', 'Explicit', 'label'),
  ('b7f068e2-48d9-4df4-967f-78780f9f91e7', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('d37ff941-eaba-4f9c-b8dc-10b61a015f6b', 'No Intro Edit', 'No Intro Edit', 'label'),
  ('c4e28180-b8d8-47e1-9e71-85d91f938679', 'Radio Edit', 'Radio Edit', 'label'),
  ('45e20b96-51e7-4383-9280-ee030e8529d6', 'Explicit', 'Explicit', 'label'),
  ('41d672a2-0f3e-435c-bec0-003899970044', 'Remix', 'Remix', 'version'),
  ('b73e1c25-9a65-4f8a-bda3-1cab22644760', 'Acoustic Version', 'Acoustic Version', 'version'),
  ('19db8dd7-ede7-4047-a7b9-471cbbf1037f', 'Edited', 'Edited', 'label'),
  ('73b78bf5-ad30-48c0-9211-c140c27c840f', 'The Voice Performance', 'The Voice Performance', 'version'),
  ('57c2067b-71d3-475f-93f6-080048ef58fe', 'Recorded at Spotify Studios NYC', 'Recorded at Spotify Studios NYC', 'version'),
  ('6a016dad-3d54-446c-85eb-6c4aff588204', 'Explicit', 'Explicit', 'label'),
  ('ba277c4a-a385-4b99-a9db-2360e73ddad6', 'Acoustic', 'Acoustic', 'version'),
  ('c8752cf2-edb2-4831-b518-e5081bbba2e0', 'Aquilo Remix', 'Aquilo Remix', 'version'),
  ('780a454a-18eb-49da-a387-57d60c3af859', 'Explicit', 'Explicit', 'label'),
  ('139cced4-186f-40a6-b2e9-be42ead01621', 'Darkchild Main Version', 'Darkchild Main Version', 'version'),
  ('8aba68bf-c13b-46a5-9253-dbf0a9eb8ddc', 'Cover - Mahogany Sessions', 'Mahogany Sessions', 'label');

-- 1. Record the Remix column on the play.
UPDATE tracks t
SET remix = b.remix
FROM remix_backfill b
WHERE t.id = b.track_id
  AND t.remix IS DISTINCT FROM b.remix;

-- 2. Where each version play belongs: its display title and the keys it is
--    matched on (title + version and main artist, letters and digits only).
CREATE TEMP TABLE remix_targets ON COMMIT DROP AS
SELECT
  t.id AS track_id,
  c.id AS from_id,
  c.owner_id,
  c.artist,
  c.artist_normalized,
  d.display,
  regexp_replace(regexp_replace(lower(d.display), '[^a-z0-9]+', '', 'g'), 'version$', '') AS title_key,
  regexp_replace(
    regexp_replace(lower(c.artist), '\s*(,|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''),
    '[^a-z0-9]+', '', 'g') AS artist_key,
  t.genre, t.bpm, t.release_year
FROM remix_backfill b
JOIN tracks t ON t.id = b.track_id
JOIN track_catalog c ON c.id = t.catalog_id
CROSS JOIN LATERAL (
  SELECT CASE WHEN position(lower(b.version) IN lower(t.title)) > 0
              THEN t.title
              ELSE t.title || ' (' || b.version || ')' END AS display
) d
WHERE b.kind = 'version';

CREATE TEMP TABLE catalog_keys ON COMMIT DROP AS
SELECT
  c.id, c.artist_normalized, c.created_at,
  regexp_replace(regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g'), 'version$', '') AS title_key,
  regexp_replace(
    regexp_replace(lower(c.artist), '\s*(,|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''),
    '[^a-z0-9]+', '', 'g') AS artist_key
FROM track_catalog c;

-- 3. Create the versions the catalog does not have yet, one entry each.
INSERT INTO track_catalog (
  id, owner_id, title, artist, title_normalized, artist_normalized,
  source, confidence, genre, bpm, release_year, play_count
)
SELECT DISTINCT ON (r.title_key, r.artist_key)
  gen_random_uuid(), r.owner_id, r.display, r.artist, lower(r.display), r.artist_normalized,
  'play_history', 'low', NULL, NULL, NULL, 0
FROM remix_targets r
WHERE NOT EXISTS (
  SELECT 1 FROM catalog_keys k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
)
ORDER BY r.title_key, r.artist_key, r.display
ON CONFLICT (owner_id, title_normalized, artist_normalized) DO NOTHING;

-- Refresh the keys so the new entries are matchable.
INSERT INTO catalog_keys
SELECT
  c.id, c.artist_normalized, c.created_at,
  regexp_replace(regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g'), 'version$', ''),
  regexp_replace(
    regexp_replace(lower(c.artist), '\s*(,|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''),
    '[^a-z0-9]+', '', 'g')
FROM track_catalog c
WHERE c.id NOT IN (SELECT id FROM catalog_keys);

-- 4. Each version play's entry: the matching one sharing its exact artist
--    first, then the oldest.
CREATE TEMP TABLE remix_moves ON COMMIT DROP AS
SELECT r.track_id, r.from_id, m.id AS to_id
FROM remix_targets r
CROSS JOIN LATERAL (
  SELECT k.id FROM catalog_keys k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
  ORDER BY (k.artist_normalized = r.artist_normalized) DESC, k.created_at, k.id
  LIMIT 1
) m
WHERE m.id <> r.from_id;

UPDATE tracks t
SET catalog_id = m.to_id
FROM remix_moves m
WHERE t.id = m.track_id;

-- New entries take their metadata from their plays.
UPDATE track_catalog c
SET genre        = COALESCE(c.genre, p.genre),
    bpm          = COALESCE(c.bpm, p.bpm),
    release_year = COALESCE(c.release_year, p.release_year)
FROM (
  SELECT t.catalog_id,
         (array_agg(t.genre) FILTER (WHERE t.genre IS NOT NULL))[1] AS genre,
         (array_agg(t.bpm) FILTER (WHERE t.bpm IS NOT NULL))[1] AS bpm,
         (array_agg(t.release_year) FILTER (WHERE t.release_year IS NOT NULL))[1] AS release_year
  FROM tracks t
  WHERE t.catalog_id IN (SELECT to_id FROM remix_moves)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;

-- 5. Recompute every touched entry from its plays; drop the ones left empty.
UPDATE track_catalog c
SET play_count   = p.plays,
    first_played = p.first_played,
    last_played  = p.last_played
FROM (
  SELECT t.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM tracks t
  JOIN sets s ON s.id = t.set_id
  WHERE t.catalog_id IN (SELECT to_id FROM remix_moves UNION SELECT from_id FROM remix_moves)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;

DELETE FROM track_catalog c
WHERE c.id IN (SELECT from_id FROM remix_moves)
  AND NOT EXISTS (SELECT 1 FROM tracks t WHERE t.catalog_id = c.id);
