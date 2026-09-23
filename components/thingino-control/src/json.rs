use std::collections::BTreeMap;

const MAX_DEPTH: usize = 32;

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Value {
    Null,
    Bool(bool),
    Number(String),
    String(String),
    Array(Vec<Value>),
    Object(BTreeMap<String, Value>),
}

impl Value {
    pub(crate) fn get_path(&self, path: &str) -> Option<&Value> {
        let mut value = self;
        for key in path.split('.') {
            value = match value {
                Self::Object(object) => object.get(key)?,
                _ => return None,
            };
        }
        Some(value)
    }

    pub(crate) fn set_path(&mut self, path: &str, replacement: Value) -> Result<(), ()> {
        let mut keys = path.split('.').peekable();
        let mut value = self;
        while let Some(key) = keys.next() {
            if keys.peek().is_none() {
                let Self::Object(object) = value else {
                    return Err(());
                };
                object.insert(key.to_owned(), replacement);
                return Ok(());
            }
            let Self::Object(object) = value else {
                return Err(());
            };
            value = object
                .entry(key.to_owned())
                .or_insert_with(|| Self::Object(BTreeMap::new()));
        }
        Err(())
    }

    pub(crate) fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            _ => None,
        }
    }

    pub(crate) fn as_str(&self) -> Option<&str> {
        match self {
            Self::String(value) => Some(value),
            _ => None,
        }
    }

    pub(crate) fn as_array(&self) -> Option<&[Value]> {
        match self {
            Self::Array(values) => Some(values),
            _ => None,
        }
    }

    pub(crate) fn as_object(&self) -> Option<&BTreeMap<String, Value>> {
        match self {
            Self::Object(value) => Some(value),
            _ => None,
        }
    }

    pub(crate) fn merge(&mut self, update: &Value) -> Result<(), ()> {
        let (Self::Object(current), Self::Object(update)) = (self, update) else {
            return Err(());
        };
        for (key, value) in update {
            if let Some(existing) = current.get_mut(key)
                && existing.as_object().is_some()
                && value.as_object().is_some()
            {
                existing.merge(value)?;
            } else {
                current.insert(key.clone(), value.clone());
            }
        }
        Ok(())
    }

    pub(crate) fn to_json(&self) -> String {
        let mut output = String::new();
        self.write_json(&mut output);
        output
    }

    fn write_json(&self, output: &mut String) {
        match self {
            Self::Null => output.push_str("null"),
            Self::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
            Self::Number(value) => output.push_str(value),
            Self::String(value) => write_string(output, value),
            Self::Array(values) => {
                output.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index != 0 {
                        output.push(',');
                    }
                    value.write_json(output);
                }
                output.push(']');
            }
            Self::Object(values) => {
                output.push('{');
                for (index, (key, value)) in values.iter().enumerate() {
                    if index != 0 {
                        output.push(',');
                    }
                    write_string(output, key);
                    output.push(':');
                    value.write_json(output);
                }
                output.push('}');
            }
        }
    }
}

fn write_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character < '\u{20}' => {
                use std::fmt::Write as _;
                let _ = write!(output, "\\u{:04x}", character as u32);
            }
            character => output.push(character),
        }
    }
    output.push('"');
}

pub(crate) fn parse(input: &[u8]) -> Result<Value, ()> {
    let mut parser = Parser { input, position: 0 };
    parser.skip_whitespace();
    let value = parser.parse_value(0)?;
    parser.skip_whitespace();
    if parser.position == input.len() {
        Ok(value)
    } else {
        Err(())
    }
}

struct Parser<'a> {
    input: &'a [u8],
    position: usize,
}

impl Parser<'_> {
    fn parse_value(&mut self, depth: usize) -> Result<Value, ()> {
        if depth > MAX_DEPTH {
            return Err(());
        }
        self.skip_whitespace();
        match self.peek().ok_or(())? {
            b'n' => {
                self.literal(b"null")?;
                Ok(Value::Null)
            }
            b't' => {
                self.literal(b"true")?;
                Ok(Value::Bool(true))
            }
            b'f' => {
                self.literal(b"false")?;
                Ok(Value::Bool(false))
            }
            b'"' => Ok(Value::String(self.parse_string()?)),
            b'[' => self.parse_array(depth + 1),
            b'{' => self.parse_object(depth + 1),
            b'-' | b'0'..=b'9' => self.parse_number(),
            _ => Err(()),
        }
    }

    fn parse_array(&mut self, depth: usize) -> Result<Value, ()> {
        self.expect(b'[')?;
        self.skip_whitespace();
        let mut values = Vec::new();
        if self.take(b']') {
            return Ok(Value::Array(values));
        }
        loop {
            values.push(self.parse_value(depth)?);
            self.skip_whitespace();
            if self.take(b']') {
                return Ok(Value::Array(values));
            }
            self.expect(b',')?;
        }
    }

    fn parse_object(&mut self, depth: usize) -> Result<Value, ()> {
        self.expect(b'{')?;
        self.skip_whitespace();
        let mut values = BTreeMap::new();
        if self.take(b'}') {
            return Ok(Value::Object(values));
        }
        loop {
            self.skip_whitespace();
            let key = self.parse_string()?;
            self.skip_whitespace();
            self.expect(b':')?;
            let value = self.parse_value(depth)?;
            if values.insert(key, value).is_some() {
                return Err(());
            }
            self.skip_whitespace();
            if self.take(b'}') {
                return Ok(Value::Object(values));
            }
            self.expect(b',')?;
        }
    }

    fn parse_string(&mut self) -> Result<String, ()> {
        self.expect(b'"')?;
        let mut output = Vec::new();
        loop {
            let byte = self.next().ok_or(())?;
            match byte {
                b'"' => return String::from_utf8(output).map_err(|_| ()),
                0..=0x1f => return Err(()),
                b'\\' => match self.next().ok_or(())? {
                    b'"' => output.push(b'"'),
                    b'\\' => output.push(b'\\'),
                    b'/' => output.push(b'/'),
                    b'b' => output.push(0x08),
                    b'f' => output.push(0x0c),
                    b'n' => output.push(b'\n'),
                    b'r' => output.push(b'\r'),
                    b't' => output.push(b'\t'),
                    b'u' => {
                        let first = self.hex_quad()?;
                        let scalar = if (0xd800..=0xdbff).contains(&first) {
                            self.expect(b'\\')?;
                            self.expect(b'u')?;
                            let second = self.hex_quad()?;
                            if !(0xdc00..=0xdfff).contains(&second) {
                                return Err(());
                            }
                            0x10000 + (((first - 0xd800) as u32) << 10) + (second - 0xdc00) as u32
                        } else if (0xdc00..=0xdfff).contains(&first) {
                            return Err(());
                        } else {
                            first as u32
                        };
                        let character = char::from_u32(scalar).ok_or(())?;
                        let mut buffer = [0_u8; 4];
                        output.extend_from_slice(character.encode_utf8(&mut buffer).as_bytes());
                    }
                    _ => return Err(()),
                },
                byte => output.push(byte),
            }
        }
    }

    fn hex_quad(&mut self) -> Result<u16, ()> {
        let mut value = 0_u16;
        for _ in 0..4 {
            let digit = match self.next().ok_or(())? {
                byte @ b'0'..=b'9' => u16::from(byte - b'0'),
                byte @ b'a'..=b'f' => u16::from(byte - b'a') + 10,
                byte @ b'A'..=b'F' => u16::from(byte - b'A') + 10,
                _ => return Err(()),
            };
            value = value * 16 + digit;
        }
        Ok(value)
    }

    fn parse_number(&mut self) -> Result<Value, ()> {
        let start = self.position;
        self.take(b'-');
        match self.peek().ok_or(())? {
            b'0' => {
                self.position += 1;
                if self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                    return Err(());
                }
            }
            b'1'..=b'9' => {
                self.position += 1;
                while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                    self.position += 1;
                }
            }
            _ => return Err(()),
        }
        if self.take(b'.') {
            if !self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                return Err(());
            }
            while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                self.position += 1;
            }
        }
        if self.peek().is_some_and(|byte| matches!(byte, b'e' | b'E')) {
            self.position += 1;
            if self.peek().is_some_and(|byte| matches!(byte, b'+' | b'-')) {
                self.position += 1;
            }
            if !self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                return Err(());
            }
            while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                self.position += 1;
            }
        }
        let number = std::str::from_utf8(&self.input[start..self.position]).map_err(|_| ())?;
        Ok(Value::Number(number.to_owned()))
    }

    fn literal(&mut self, literal: &[u8]) -> Result<(), ()> {
        if self.input.get(self.position..self.position + literal.len()) == Some(literal) {
            self.position += literal.len();
            Ok(())
        } else {
            Err(())
        }
    }

    fn skip_whitespace(&mut self) {
        while self
            .peek()
            .is_some_and(|byte| matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        {
            self.position += 1;
        }
    }

    fn expect(&mut self, expected: u8) -> Result<(), ()> {
        if self.take(expected) { Ok(()) } else { Err(()) }
    }

    fn take(&mut self, expected: u8) -> bool {
        if self.peek() == Some(expected) {
            self.position += 1;
            true
        } else {
            false
        }
    }

    fn peek(&self) -> Option<u8> {
        self.input.get(self.position).copied()
    }

    fn next(&mut self) -> Option<u8> {
        let value = self.peek()?;
        self.position += 1;
        Some(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_updates_and_serializes_nested_config() {
        let mut value = parse(br#"{"daynight":{"enabled":false,"force_mode":"day"},"unicode":"\ud83d\udcf7","number":1.25e2}"#).unwrap();
        assert_eq!(
            value.get_path("daynight.enabled").and_then(Value::as_bool),
            Some(false)
        );
        value
            .set_path("daynight.enabled", Value::Bool(true))
            .unwrap();
        value
            .set_path("daynight.force_mode", Value::String(String::new()))
            .unwrap();
        let reparsed = parse(value.to_json().as_bytes()).unwrap();
        assert_eq!(
            reparsed
                .get_path("daynight.enabled")
                .and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            reparsed.get_path("unicode"),
            Some(&Value::String("📷".to_owned()))
        );
        assert_eq!(
            reparsed.get_path("number"),
            Some(&Value::Number("1.25e2".to_owned()))
        );
    }

    #[test]
    fn rejects_duplicate_keys_bad_numbers_and_excess_depth() {
        assert!(parse(br#"{"a":1,"a":2}"#).is_err());
        assert!(parse(b"01").is_err());
        assert!(parse(b"1.").is_err());
        assert!(parse(format!("{}0{}", "[".repeat(34), "]".repeat(34)).as_bytes()).is_err());
    }

    #[test]
    fn parser_depth_boundary_is_inclusive_only_at_the_limit() {
        let at_limit = format!("{}0{}", "[".repeat(MAX_DEPTH), "]".repeat(MAX_DEPTH));
        let beyond_limit = format!(
            "{}0{}",
            "[".repeat(MAX_DEPTH + 1),
            "]".repeat(MAX_DEPTH + 1)
        );
        assert!(parse(at_limit.as_bytes()).is_ok());
        assert!(parse(beyond_limit.as_bytes()).is_err());
    }

    #[test]
    fn parser_rejects_invalid_unicode_escapes_and_trailing_input() {
        for invalid in [
            br#""\ud800""#.as_slice(),
            br#""\udc00""#.as_slice(),
            br#""\ud800\u0041""#.as_slice(),
            br#""\u12g4""#.as_slice(),
            br#""\x41""#.as_slice(),
            b"\"line\nbreak\"".as_slice(),
            b"\"\xff\"".as_slice(),
            b"true false".as_slice(),
            b"[] trailing".as_slice(),
        ] {
            assert!(parse(invalid).is_err(), "accepted {invalid:?}");
        }
    }

    #[test]
    fn parser_accepts_json_number_edges_and_rejects_incomplete_forms() {
        for valid in ["0", "-0", "10", "-12.5", "1e0", "1E+9", "1e-9"] {
            assert_eq!(
                parse(valid.as_bytes()),
                Ok(Value::Number(valid.to_owned())),
                "rejected {valid}"
            );
        }
        for invalid in ["-", "+1", "00", "-01", ".1", "1.", "1e", "1e+", "NaN"] {
            assert!(parse(invalid.as_bytes()).is_err(), "accepted {invalid}");
        }
    }
}
