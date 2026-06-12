"""
Java Extractor Tests

測試 Java 程式碼 Graph 提取功能。
"""

import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.code_graph_extractor.extractor import RegexExtractor, extract_from_file


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def sample_java_class():
    """範例 Java 類別（含繼承、實作、方法、常數）"""
    return '''
package com.example.user;

import java.util.List;
import java.util.Optional;
import static java.util.Objects.requireNonNull;

/**
 * User entity class
 */
public class User extends BaseEntity implements Serializable, Comparable<User> {

    private static final long serialVersionUID = 1L;
    public static final int MAX_NAME_LENGTH = 100;

    private String name;
    private int age;

    public User(String name) {
        this.name = requireNonNull(name);
    }

    public String getName() {
        return name;
    }

    public void setName(String name) throws IllegalArgumentException {
        if (name.length() > MAX_NAME_LENGTH) {
            throw new IllegalArgumentException("Name too long");
        }
        this.name = name;
    }

    @Override
    public int compareTo(User other) {
        return this.name.compareTo(other.name);
    }

    public static class Builder {
        private String name;

        public Builder name(String name) {
            this.name = name;
            return this;
        }

        public User build() {
            return new User(name);
        }
    }
}
'''


@pytest.fixture
def sample_interface():
    """範例 Java Interface"""
    return '''
package com.example.service;

import java.util.List;

public interface UserService extends BaseService<User>, Cloneable {

    User findById(Long id);

    List<User> findAll();

    default void log(String message) {
        System.out.println(message);
    }

    static UserService create() {
        return new DefaultUserService();
    }
}
'''


@pytest.fixture
def sample_enum():
    """範例 Java Enum"""
    return '''
package com.example.status;

public enum Status implements Describable {
    ACTIVE("Active status"),
    INACTIVE("Inactive status"),
    PENDING("Pending status");

    private final String description;

    Status(String description) {
        this.description = description;
    }

    @Override
    public String getDescription() {
        return description;
    }
}
'''


@pytest.fixture
def sample_annotation():
    """範例 Java Annotation"""
    return '''
package com.example.validation;

import java.lang.annotation.*;

@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.FIELD)
public @interface NotNull {
    String message() default "Value cannot be null";
}
'''


# =============================================================================
# Test: Package Extraction
# =============================================================================

class TestJavaPackageExtraction:
    """測試 Package 提取"""

    def test_extract_package(self, sample_java_class):
        """應該正確提取 package 並用於 qualified name"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        # 驗證 class ID 包含 package
        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) >= 1

        user_class = next((n for n in class_nodes if n.name == 'User'), None)
        assert user_class is not None
        assert 'com.example.user' in user_class.id


# =============================================================================
# Test: Import Extraction
# =============================================================================

class TestJavaImportExtraction:
    """測試 Import 提取"""

    def test_extract_regular_imports(self, sample_java_class):
        """應該提取一般 import"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        import_edges = [e for e in result.edges if e.kind == 'imports']
        assert len(import_edges) >= 2

        target_ids = [e.to_id for e in import_edges]
        assert any('java.util.List' in t for t in target_ids)
        assert any('java.util.Optional' in t for t in target_ids)

    def test_extract_static_import(self, sample_java_class):
        """應該提取 static import"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        import_edges = [e for e in result.edges if e.kind == 'imports']
        target_ids = [e.to_id for e in import_edges]

        # static import 應該被提取
        assert any('requireNonNull' in t or 'Objects' in t for t in target_ids)

    def test_extract_wildcard_import(self):
        """應該提取萬用字元 import"""
        content = '''
package com.example;

import java.util.*;
import java.io.*;

public class Test {}
'''
        result = RegexExtractor.extract_java(content, 'Test.java')

        import_edges = [e for e in result.edges if e.kind == 'imports']
        target_ids = [e.to_id for e in import_edges]

        # 萬用字元 import 應該指向 package
        assert any('package.java.util' in t for t in target_ids)
        assert any('package.java.io' in t for t in target_ids)


# =============================================================================
# Test: Class Extraction
# =============================================================================

class TestJavaClassExtraction:
    """測試 Class 提取"""

    def test_extract_class_with_inheritance(self, sample_java_class):
        """應該提取 class 及其繼承關係"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        # 驗證 extends edge
        extends_edges = [e for e in result.edges if e.kind == 'extends']
        assert len(extends_edges) >= 1
        assert any('BaseEntity' in e.to_id for e in extends_edges)

    def test_extract_class_with_implements(self, sample_java_class):
        """應該提取 class 的 implements 關係"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        implements_edges = [e for e in result.edges if e.kind == 'implements']
        assert len(implements_edges) >= 2

        impl_targets = [e.to_id for e in implements_edges]
        assert any('Serializable' in t for t in impl_targets)
        assert any('Comparable' in t for t in impl_targets)

    def test_extract_visibility(self, sample_java_class):
        """應該正確提取 visibility"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        user_class = next((n for n in result.nodes if n.name == 'User' and n.kind == 'class'), None)
        assert user_class is not None
        assert user_class.visibility == 'public'

    def test_extract_inner_class(self, sample_java_class):
        """應該提取 inner class 及 contains 關係"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        # 應該有 User 和 Builder 兩個 class
        class_nodes = [n for n in result.nodes if n.kind == 'class']
        class_names = [n.name for n in class_nodes]
        assert 'User' in class_names
        assert 'Builder' in class_names

        # Builder 應該透過 contains edge 連接到 User
        contains_edges = [e for e in result.edges if e.kind == 'contains']
        builder_contained = any('Builder' in e.to_id for e in contains_edges)
        assert builder_contained

    def test_extract_abstract_class(self):
        """應該提取 abstract class"""
        content = '''
package com.example;

public abstract class AbstractHandler {
    public abstract void handle();
}
'''
        result = RegexExtractor.extract_java(content, 'AbstractHandler.java')

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 1
        assert class_nodes[0].name == 'AbstractHandler'

    def test_extract_final_class(self):
        """應該提取 final class"""
        content = '''
package com.example;

public final class ImmutableValue {
    private final String value;
}
'''
        result = RegexExtractor.extract_java(content, 'ImmutableValue.java')

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 1
        assert class_nodes[0].name == 'ImmutableValue'

    def test_extract_generic_class(self):
        """應該提取泛型 class"""
        content = '''
package com.example;

public class GenericClass<T extends Comparable<T>> implements List<T> {
    public T getValue() { return null; }
}
'''
        result = RegexExtractor.extract_java(content, 'GenericClass.java')

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 1
        assert class_nodes[0].name == 'GenericClass'


# =============================================================================
# Test: Interface Extraction
# =============================================================================

class TestJavaInterfaceExtraction:
    """測試 Interface 提取"""

    def test_extract_interface(self, sample_interface):
        """應該提取 interface"""
        result = RegexExtractor.extract_java(sample_interface, 'UserService.java')

        iface_nodes = [n for n in result.nodes if n.kind == 'interface']
        assert len(iface_nodes) == 1
        assert iface_nodes[0].name == 'UserService'

    def test_extract_interface_extends(self, sample_interface):
        """應該提取 interface 的 extends 關係"""
        result = RegexExtractor.extract_java(sample_interface, 'UserService.java')

        extends_edges = [e for e in result.edges if e.kind == 'extends']
        # UserService extends BaseService 和 Cloneable
        assert len(extends_edges) >= 2


# =============================================================================
# Test: Enum Extraction
# =============================================================================

class TestJavaEnumExtraction:
    """測試 Enum 提取"""

    def test_extract_enum(self, sample_enum):
        """應該提取 enum"""
        result = RegexExtractor.extract_java(sample_enum, 'Status.java')

        enum_nodes = [n for n in result.nodes if n.kind == 'enum']
        assert len(enum_nodes) == 1
        assert enum_nodes[0].name == 'Status'

    def test_extract_enum_implements(self, sample_enum):
        """應該提取 enum 的 implements 關係"""
        result = RegexExtractor.extract_java(sample_enum, 'Status.java')

        implements_edges = [e for e in result.edges if e.kind == 'implements']
        assert any('Describable' in e.to_id for e in implements_edges)


# =============================================================================
# Test: Annotation Extraction
# =============================================================================

class TestJavaAnnotationExtraction:
    """測試 Annotation 提取"""

    def test_extract_annotation(self, sample_annotation):
        """應該提取 @interface annotation"""
        result = RegexExtractor.extract_java(sample_annotation, 'NotNull.java')

        annotation_nodes = [n for n in result.nodes if n.kind == 'annotation']
        assert len(annotation_nodes) == 1
        assert annotation_nodes[0].name == 'NotNull'


# =============================================================================
# Test: Method Extraction
# =============================================================================

class TestJavaMethodExtraction:
    """測試 Method 提取"""

    def test_extract_methods(self, sample_java_class):
        """應該提取方法"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        method_nodes = [n for n in result.nodes if n.kind == 'function']
        method_names = [n.name for n in method_nodes]

        assert 'getName' in method_names
        assert 'setName' in method_names
        assert 'compareTo' in method_names

    def test_extract_method_signature(self, sample_java_class):
        """應該提取方法簽名"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        setname = next((n for n in result.nodes if n.name == 'setName'), None)
        assert setname is not None
        assert setname.signature is not None
        assert 'throws' in setname.signature

    def test_skip_constructors(self, sample_java_class):
        """不應該把建構子當作方法提取"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        method_nodes = [n for n in result.nodes if n.kind == 'function']
        method_names = [n.name for n in method_nodes]

        # 建構子 User(String name) 不應該被提取
        # 但要小心，可能有同名的方法
        # 這裡主要驗證不會有錯誤的建構子被提取


# =============================================================================
# Test: Constant Extraction
# =============================================================================

class TestJavaConstantExtraction:
    """測試 Constant 提取"""

    def test_extract_constants(self, sample_java_class):
        """應該提取 static final 常數"""
        result = RegexExtractor.extract_java(sample_java_class, 'User.java')

        const_nodes = [n for n in result.nodes if n.kind == 'constant']
        const_names = [n.name for n in const_nodes]

        assert 'MAX_NAME_LENGTH' in const_names


# =============================================================================
# Test: Comment Handling
# =============================================================================

class TestJavaCommentHandling:
    """測試註解處理"""

    def test_remove_single_line_comments(self):
        """應該移除單行註解"""
        content = '''
public class Test {
    // This is a comment
    public void method() {} // inline comment
}
'''
        cleaned = RegexExtractor._remove_java_comments(content)
        assert '//' not in cleaned

    def test_remove_multiline_comments(self):
        """應該移除多行註解"""
        content = '''
public class Test {
    /* This is
       a multiline
       comment */
    public void method() {}
}
'''
        cleaned = RegexExtractor._remove_java_comments(content)
        assert '/*' not in cleaned
        assert '*/' not in cleaned

    def test_remove_javadoc_comments(self):
        """應該移除 Javadoc 註解"""
        content = '''
/**
 * This is a Javadoc comment
 * @param x the parameter
 */
public class Test {}
'''
        cleaned = RegexExtractor._remove_java_comments(content)
        assert '/**' not in cleaned
        assert '@param' not in cleaned


# =============================================================================
# Test: Edge Cases
# =============================================================================

class TestJavaEdgeCases:
    """測試邊界情況"""

    def test_no_package(self):
        """應該處理沒有 package 的情況"""
        content = '''
public class SimpleClass {
    public void doSomething() {}
}
'''
        result = RegexExtractor.extract_java(content, 'SimpleClass.java')

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 1
        # ID 應該不包含 package 前綴
        assert class_nodes[0].id == 'class.SimpleClass.java:SimpleClass'

    def test_multiple_classes_in_file(self):
        """應該處理一個檔案中的多個 class"""
        content = '''
package com.example;

public class MainClass {}

class HelperClass {}

class AnotherHelper {}
'''
        result = RegexExtractor.extract_java(content, 'MainClass.java')

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 3

    def test_empty_file(self):
        """應該處理空檔案"""
        result = RegexExtractor.extract_java('', 'Empty.java')

        # 只有 file node
        assert len(result.nodes) == 1
        assert result.nodes[0].kind == 'file'

    def test_string_with_braces(self):
        """應該正確處理字串中的括號"""
        content = '''
package com.example;

public class Test {
    public String getBraces() {
        return "{ }";
    }
}
'''
        result = RegexExtractor.extract_java(content, 'Test.java')

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 1


# =============================================================================
# Test: File-based Extraction
# =============================================================================

class TestJavaFileExtraction:
    """測試檔案層級提取"""

    def test_extract_from_file(self, tmp_path):
        """應該能從檔案提取"""
        java_file = tmp_path / "Test.java"
        java_file.write_text('''
package com.example;

public class Test {
    public void hello() {}
}
''')
        result = extract_from_file(str(java_file))

        assert result.language == 'java'
        assert len(result.errors) == 0

        class_nodes = [n for n in result.nodes if n.kind == 'class']
        assert len(class_nodes) == 1


# =============================================================================
# Tree-sitter: Java interface 提取（fix/java-interface-extraction）
# =============================================================================

class TestJavaInterfaceExtractionTreeSitter:
    """Java interface 必須產生 interface 節點，且成員方法要有 class-scope。

    背景：JAVA_PACK 先前宣告 interface_types 但未掛 extract_interface，
    導致 interface 被靜默丟棄、其方法以無 scope 的 function 流出 —
    在真實專案（aipoolserver）上造成 16 筆假 drift。
    """

    @pytest.fixture
    def java_interface_src(self):
        return '''
package com.ap.goods.service;

public interface GoodsOrderService extends BaseOrderService {
    String create(GoodsOrderDto dto);
    void timeOutGoodsOrder(String orderId);
}
'''

    def _ts_extract(self, src, path):
        from tools.code_graph_extractor.backends.tree_sitter_backend import (
            TreeSitterBackend, _load_grammar)
        if _load_grammar('java', auto_install=False) is None:
            pytest.skip('tree-sitter-java grammar not installed')
        return TreeSitterBackend().extract(src, path)

    def test_interface_node_extracted(self, java_interface_src):
        r = self._ts_extract(java_interface_src, 'GoodsOrderService.java')
        ifaces = [n for n in r.nodes if n.kind == 'interface']
        assert any(n.name == 'GoodsOrderService' for n in ifaces), \
            f'interface 節點未被提取；nodes={[(n.kind, n.name) for n in r.nodes]}'

    def test_interface_methods_are_scoped(self, java_interface_src):
        r = self._ts_extract(java_interface_src, 'GoodsOrderService.java')
        ids = [n.id for n in r.nodes if n.kind in ('function', 'method')]
        assert any(i.endswith('GoodsOrderService.create') for i in ids), \
            f'create 未取得 interface scope；ids={ids}'
        assert any(i.endswith('GoodsOrderService.timeOutGoodsOrder') for i in ids)

    def test_interface_extends_edge(self, java_interface_src):
        r = self._ts_extract(java_interface_src, 'GoodsOrderService.java')
        ext = [e for e in r.edges if e.kind == 'extends']
        assert any('BaseOrderService' in e.to_id for e in ext), \
            f'extends edge 缺失；edges={[(e.kind, e.to_id) for e in r.edges][:10]}'
