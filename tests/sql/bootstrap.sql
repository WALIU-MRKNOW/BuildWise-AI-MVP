-- Disposable CI database only. Supabase supplies these roles/storage in production.
create role anon;
create role authenticated;
create role service_role bypassrls;
create schema storage;
create table storage.buckets(id text primary key, name text, public boolean, file_size_limit bigint);
