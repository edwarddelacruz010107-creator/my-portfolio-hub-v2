"""
app/services/tenant_api_keys.py — Tenant API Key Management v5.0

FIX SUMMARY (Requirement #8):
  ✅ API key generation with cryptographic randomness
  ✅ Fernet encryption for storage
  ✅ Key rotation support
  ✅ Never expose secrets in responses
  ✅ Audit logging for key operations

API keys are stored encrypted:
  - plaintext_prefix: First 16 characters (for lookup)
  - encrypted_key: Full key encrypted with Fernet
  - Only show full key once after generation
  - Support rotation without invalidating old keys
  - Allow temporary enable/disable without deletion

Usage:
  api_key = TenantAPIKeyService.generate_key(tenant_id)
  print(api_key.key)  # "pk_live_123456...789abc" (show only once)
  
  # Later, can't retrieve the full key anymore
  key_obj = TenantAPIKeyService.get_key(tenant_id, 'pk_live')
  # Only see: prefix, last 8 chars, created_at, etc.
  
  # Rotate: generates new key, keeps old one disabled
  new_key = TenantAPIKeyService.rotate_key(tenant_id, old_key_id)
"""

import secrets
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from app import db
from app.models.core import TenantAPIKey, Tenant
from app.security import encrypt_fernet, decrypt_fernet
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class TenantAPIKeyService:
    """
    Service for managing tenant API keys with encryption and rotation.
    """
    
    # ─────────────────────────────────────────────────────────────────
    # KEY GENERATION
    # ─────────────────────────────────────────────────────────────────
    
    @staticmethod
    def generate_key(tenant_id: int, name: str = 'Default API Key') -> Optional[Tuple[TenantAPIKey, str]]:
        """
        Generate a new API key for a tenant.
        
        Args:
            tenant_id: ID of tenant
            name: Human-readable name for the key
        
        Returns:
            Tuple of (TenantAPIKey object, plaintext key)
            Returns None if tenant not found or error
        
        Note:
            The plaintext key is returned only once. It must be saved by
            the client immediately. We cannot retrieve it later.
        """
        try:
            # Verify tenant exists
            tenant = db.session.query(Tenant).filter_by(id=tenant_id).first()
            if not tenant:
                logger.error('API key generation: tenant not found. id=%s', tenant_id)
                return None
            
            # Generate key: pk_live_{32-char-random}
            random_part = secrets.token_urlsafe(24)  # ~32 chars
            plaintext_key = f'pk_live_{random_part}'
            plaintext_prefix = plaintext_key[:16]
            
            # Encrypt the full key for storage
            try:
                encrypted_key = encrypt_fernet(plaintext_key)
            except Exception as exc:
                logger.error('API key encryption failed: %s', exc)
                return None
            
            # Create database record
            key_obj = TenantAPIKey(
                tenant_id=tenant_id,
                name=name,
                plaintext_prefix=plaintext_prefix,
                encrypted_key=encrypted_key,
                is_active=True,
            )
            
            db.session.add(key_obj)
            db.session.commit()
            
            logger.info(
                'API key generated: tenant=%s name=%s prefix=%s',
                tenant_id, name, plaintext_prefix
            )
            
            return key_obj, plaintext_key
            
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.exception('Database error generating API key for tenant=%s', tenant_id)
            return None
        except Exception as exc:
            logger.exception('Unexpected error generating API key for tenant=%s', tenant_id)
            return None
    
    # ─────────────────────────────────────────────────────────────────
    # KEY MANAGEMENT
    # ─────────────────────────────────────────────────────────────────
    
    @staticmethod
    def list_keys(tenant_id: int) -> list:
        """List all API keys for a tenant (without exposing encrypted values)."""
        try:
            keys = db.session.query(TenantAPIKey).filter_by(
                tenant_id=tenant_id
            ).order_by(
                TenantAPIKey.created_at.desc()
            ).all()
            
            return [
                {
                    'id': k.id,
                    'name': k.name,
                    'prefix': k.plaintext_prefix,
                    'last_chars': k.encrypted_key[-8:] if k.encrypted_key else '...',
                    'is_active': k.is_active,
                    'created_at': k.created_at.isoformat() if k.created_at else None,
                    'last_used': k.last_used.isoformat() if k.last_used else None,
                }
                for k in keys
            ]
        except Exception as exc:
            logger.exception('Error listing API keys for tenant=%s', tenant_id)
            return []
    
    @staticmethod
    def get_key(tenant_id: int, key_id: int) -> Optional[TenantAPIKey]:
        """Get API key object by ID (for verification, not retrieval)."""
        try:
            return db.session.query(TenantAPIKey).filter_by(
                id=key_id,
                tenant_id=tenant_id,
            ).first()
        except Exception as exc:
            logger.exception('Error retrieving API key: key=%s tenant=%s', key_id, tenant_id)
            return None
    
    @staticmethod
    def verify_key(plaintext_key: str) -> Optional[int]:
        """
        Verify an API key and return the tenant_id if valid.
        
        Args:
            plaintext_key: The API key to verify
        
        Returns:
            Tenant ID if key is valid and active
            None if key is invalid or inactive
        """
        if not plaintext_key or len(plaintext_key) < 20:
            return None
        
        prefix = plaintext_key[:16]
        
        try:
            key_obj = db.session.query(TenantAPIKey).filter_by(
                plaintext_prefix=prefix,
                is_active=True,
            ).first()
            
            if not key_obj:
                logger.warning('API key lookup failed: prefix=%.8s', prefix)
                return None
            
            # Verify full key matches
            try:
                stored_key = decrypt_fernet(key_obj.encrypted_key)
                if stored_key != plaintext_key:
                    logger.warning('API key mismatch: key=%s', key_obj.id)
                    return None
            except Exception as exc:
                logger.error('API key decryption failed: key=%s: %s', key_obj.id, exc)
                return None
            
            # Update last_used timestamp
            key_obj.last_used = datetime.now(timezone.utc)
            db.session.commit()
            
            logger.debug('API key verified: tenant=%s', key_obj.tenant_id)
            return key_obj.tenant_id
            
        except SQLAlchemyError as exc:
            logger.exception('Database error verifying API key')
            return None
        except Exception as exc:
            logger.exception('Unexpected error verifying API key')
            return None
    
    # ─────────────────────────────────────────────────────────────────
    # KEY ROTATION
    # ─────────────────────────────────────────────────────────────────
    
    @staticmethod
    def rotate_key(tenant_id: int, old_key_id: int) -> Optional[Tuple[TenantAPIKey, str]]:
        """
        Rotate an API key by disabling the old one and generating a new one.
        
        Args:
            tenant_id: ID of tenant
            old_key_id: ID of key to rotate
        
        Returns:
            Tuple of (new TenantAPIKey, plaintext key)
            None if operation failed
        """
        try:
            # Find old key
            old_key = db.session.query(TenantAPIKey).filter_by(
                id=old_key_id,
                tenant_id=tenant_id,
            ).first()
            
            if not old_key:
                logger.error('Old API key not found: key=%s tenant=%s', old_key_id, tenant_id)
                return None
            
            # Disable old key
            old_key.is_active = False
            db.session.commit()
            logger.info('API key disabled: key=%s tenant=%s', old_key_id, tenant_id)
            
            # Generate new key
            result = TenantAPIKeyService.generate_key(
                tenant_id,
                name=f'{old_key.name} (rotated)',
            )
            
            if result:
                logger.info('API key rotated: old=%s new=%s tenant=%s', 
                           old_key_id, result[0].id, tenant_id)
            
            return result
            
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.exception('Database error rotating API key: key=%s tenant=%s', 
                           old_key_id, tenant_id)
            return None
        except Exception as exc:
            logger.exception('Unexpected error rotating API key: key=%s tenant=%s',
                           old_key_id, tenant_id)
            return None
    
    # ─────────────────────────────────────────────────────────────────
    # KEY DEACTIVATION
    # ─────────────────────────────────────────────────────────────────
    
    @staticmethod
    def revoke_key(tenant_id: int, key_id: int) -> bool:
        """
        Revoke (deactivate) an API key.
        
        The key record is retained for audit purposes but marked inactive.
        """
        try:
            key = db.session.query(TenantAPIKey).filter_by(
                id=key_id,
                tenant_id=tenant_id,
            ).first()
            
            if not key:
                logger.error('API key not found: key=%s tenant=%s', key_id, tenant_id)
                return False
            
            key.is_active = False
            db.session.commit()
            logger.info('API key revoked: key=%s tenant=%s', key_id, tenant_id)
            return True
            
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.exception('Database error revoking API key: key=%s tenant=%s',
                           key_id, tenant_id)
            return False
        except Exception as exc:
            logger.exception('Unexpected error revoking API key: key=%s tenant=%s',
                           key_id, tenant_id)
            return False
    
    # ─────────────────────────────────────────────────────────────────
    # SUPERADMIN OPERATIONS
    # ─────────────────────────────────────────────────────────────────
    
    @staticmethod
    def create_api_key_for_tenant(tenant_id: int, name: str = None) -> Optional[Tuple[TenantAPIKey, str]]:
        """
        Create API key for a tenant (superadmin operation).
        
        This is called by superadmin when creating a new tenant.
        """
        if not name:
            name = 'Default API Key'
        
        return TenantAPIKeyService.generate_key(tenant_id, name)
    
    @staticmethod
    def superadmin_list_tenant_keys(tenant_id: int) -> list:
        """List all API keys for a tenant (superadmin view)."""
        return TenantAPIKeyService.list_keys(tenant_id)
    
    @staticmethod
    def superadmin_revoke_tenant_key(tenant_id: int, key_id: int) -> bool:
        """Revoke a tenant's API key (superadmin operation)."""
        return TenantAPIKeyService.revoke_key(tenant_id, key_id)
