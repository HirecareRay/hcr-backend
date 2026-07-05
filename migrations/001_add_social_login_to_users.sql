-- 001_add_social_login_to_users.sql
--
-- 소셜 로그인(카카오·구글·네이버)을 위한 users 테이블 변경.
--
-- 배경: 이 프로젝트는 아직 Alembic 이 없고, 앱 부팅 시 Base.metadata.create_all()
--       로 "없는 테이블만" 생성한다. create_all 은 이미 존재하는 테이블에 컬럼을
--       추가(ALTER)하지 못하므로, 기존 users 테이블에는 이 SQL 을 한 번 직접 실행한다.
--       (신규/CI 환경은 create_all 이 새 스키마로 만들어 이 파일이 필요 없다.)
--
-- 실행 방법 (EC2 MariaDB 컨테이너 기준, 한 번만):
--   docker exec -i <mariadb_container> mariadb -u <user> -p<password> <db명> \
--     < migrations/001_add_social_login_to_users.sql
--   또는 DB 클라이언트에서 아래 내용을 그대로 붙여 실행.
--
-- 멱등(idempotent): IF NOT EXISTS 로 여러 번 실행해도 안전하다.

-- 1) 소셜 유저는 비밀번호가 없으므로 password_hash 를 NULL 허용으로 완화.
ALTER TABLE users
  MODIFY COLUMN password_hash VARCHAR(255) NULL;

-- 2) 가입 경로 컬럼. 이메일 가입 유저는 NULL, 소셜 유저는 'kakao'·'google'·'naver'.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS provider VARCHAR(20) NULL;

-- 3) provider 안에서의 유저 고유 id(문자열 통일 — 카카오 숫자·네이버 문자열 수용).
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS provider_id VARCHAR(255) NULL;

-- 4) (provider, provider_id) 조합 유일 — 같은 소셜 계정 중복 가입을 DB 가 막는다.
--    이메일 가입 유저는 둘 다 NULL 이라 이 제약에 걸리지 않는다(NULL 은 중복 허용).
ALTER TABLE users
  ADD UNIQUE KEY IF NOT EXISTS uq_users_provider_identity (provider, provider_id);
